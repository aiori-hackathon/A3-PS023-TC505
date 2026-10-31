# CleanSlot SafeCore - Fault Injection Test Suite
# Tests worker fault mode configuration via POST /internal/fault (none or boot_fail).
# Verifies that FAILED_BOOT mock processes hold host memory until scoped cleanup.
# Confirms controller transitions failed creations to FAILED_CLEANUP_NEEDED with reservations held.

from fastapi.testclient import TestClient
from controller.database import get_vm, get_reserved_memory_by_host, get_events


def test_fault_mode_configuration(worker_client_test: TestClient, auth_headers):
    """Verify setting fault modes via POST /internal/fault."""
    # Set to boot_fail
    resp = worker_client_test.post(
        "/internal/fault", json={"fault": "boot_fail"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["fault"] == "boot_fail"

    # Reset to none
    resp = worker_client_test.post(
        "/internal/fault", json={"fault": "none"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["fault"] == "none"


def test_worker_mem_available_accounts_for_failed_boot(setup_test_environment):
    """
    Correction 3: mem_available calculation must subtract the memory of ALL VMs
    whose fake process still exists, including VMs in FAILED_BOOT state.
    """
    w1_state = setup_test_environment["worker_1_state"]
    initial_avail = w1_state.mem_available_mib

    # Spawn process in FAILED_BOOT state
    proc = w1_state.spawn_process("vm-failed-accounting", 2, 1024, "dev", status="FAILED_BOOT")
    after_spawn_avail = w1_state.mem_available_mib

    # Available memory must decrease by exactly 1024 MiB
    assert after_spawn_avail == initial_avail - 1024

    # Cleanup the process
    w1_state.execute_stop_or_cleanup(
        "vm-failed-accounting", "CLEANUP", proc.pid, proc.start_time, proc.cmdline
    )
    assert w1_state.mem_available_mib == initial_avail


def test_boot_fail_lifecycle_and_recovery(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Simulate worker boot_fail fault:
    1. VM creation fails on worker.
    2. Controller marks VM as FAILED_CLEANUP_NEEDED.
    3. Controller holds memory reservation.
    4. Scoped cleanup verifies identity, releases reservation, transitions to STOPPED_CLEAN.
    """
    db_path = setup_test_environment["db_path"]
    w1_state = setup_test_environment["worker_1_state"]
    w2_state = setup_test_environment["worker_2_state"]

    # Pre-occupy worker-2 so worker-1 is selected for placement
    w2_state.spawn_process("vm-fill-w2", 2, 3000, "admin")

    # Set worker-1 fault mode to boot_fail
    w1_state.fault_mode = "boot_fail"

    # Attempt VM creation
    resp = client.post(
        "/vms",
        json={"name": "failing-vm", "owner_label": "ci", "vcpu": 2, "memory_mib": 1024},
        headers=auth_headers,
    )
    assert resp.status_code == 500
    err_data = resp.json()
    vm_id = err_data["id"]
    assert err_data["label"] == "FAILED_CLEANUP_NEEDED"

    # Verify database state
    vm_row = get_vm(db_path, vm_id)
    assert vm_row["label"] == "FAILED_CLEANUP_NEEDED"

    # Memory reservation must remain held (Rule 13)
    reserved = get_reserved_memory_by_host(db_path, "worker-1")
    assert reserved >= 1024

    # Audit events must record CREATING -> FAILED_CLEANUP_NEEDED
    events = get_events(db_path, vm_id)
    assert events[0]["new_state"] == "CREATING"
    assert events[1]["new_state"] == "FAILED_CLEANUP_NEEDED"

    # Now execute scoped cleanup to recover capacity
    cleanup_resp = client.post(f"/vms/{vm_id}/cleanup", json={}, headers=auth_headers)
    assert cleanup_resp.status_code == 200
    receipt = cleanup_resp.json()["receipt"]
    assert receipt["result"] == "SUCCESS"
    assert receipt["reservation_released_mib"] == 1024

    # DB updated to STOPPED_CLEAN
    vm_row_after = get_vm(db_path, vm_id)
    assert vm_row_after["label"] == "STOPPED_CLEAN"

    # Reservation released
    assert get_reserved_memory_by_host(db_path, "worker-1") == reserved - 1024
