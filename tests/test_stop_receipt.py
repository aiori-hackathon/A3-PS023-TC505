# CleanSlot SafeCore - Graceful Stop and Receipt Test Suite
# Tests VM termination with strict 3-field process identity checks (pid, start_time, cmdline).
# Verifies receipt schema formatting, memory reservation release, and transition to STOPPED_CLEAN.
# Confirms that process identity mismatches abort termination and transition to UNKNOWN.

import json
from fastapi.testclient import TestClient
from controller.database import get_vm, get_events, get_reserved_memory_by_host


def test_graceful_stop_and_receipt_verification(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Ensure POST /vms/{id}/stop terminates mock VM, verifies 3-field identity,
    transitions state to STOPPED_CLEAN, releases reservation, and returns exact receipt.
    """
    db_path = setup_test_environment["db_path"]

    # 1. Create a VM
    create_resp = client.post(
        "/vms",
        json={
            "name": "stop-target-vm",
            "owner_label": "ops",
            "vcpu": 2,
            "memory_mib": 1024,
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    vm_data = create_resp.json()
    vm_id = vm_data["id"]
    host = vm_data["host"]

    # Reservation is active before stop
    reserved_before = get_reserved_memory_by_host(db_path, host)
    assert reserved_before >= 1024

    # 2. Stop the VM
    stop_resp = client.post(f"/vms/{vm_id}/stop", json={}, headers=auth_headers)
    assert stop_resp.status_code == 200
    stop_data = stop_resp.json()

    # 3. Verify receipt fields (Correction 5)
    receipt = stop_data["receipt"]
    assert receipt["vm_id"] == vm_id
    assert receipt["action"] == "STOP"
    assert receipt["result"] == "SUCCESS"
    assert receipt["checks"]["pid_found"] is True
    assert receipt["checks"]["identity_matched"] is True
    assert receipt["checks"]["process_absent_after"] is True
    assert receipt["checks"]["record_removed"] is True
    assert (
        receipt["host_available_mib"]["after"]
        > receipt["host_available_mib"]["before"]
    )
    assert receipt["reservation_released_mib"] == 1024

    # 4. Verify DB state transition to STOPPED_CLEAN
    vm_row = get_vm(db_path, vm_id)
    assert vm_row["label"] == "STOPPED_CLEAN"

    # 5. Verify reservation released
    reserved_after = get_reserved_memory_by_host(db_path, host)
    assert reserved_after == reserved_before - 1024

    # 6. Verify receipt stored in events table
    events = get_events(db_path, vm_id)
    stop_event = next(e for e in events if e["action"] == "STOP")
    assert stop_event["new_state"] == "STOPPED_CLEAN"
    assert stop_event["receipt_json"] is not None
    stored_receipt = json.loads(stop_event["receipt_json"])
    assert stored_receipt["vm_id"] == vm_id


def test_stop_non_active_vm_rejected(client: TestClient, auth_headers):
    """Ensure stopping an already STOPPED_CLEAN VM returns 400 Bad Request."""
    create_resp = client.post(
        "/vms",
        json={"name": "vm-stop-twice", "owner_label": "qa", "vcpu": 1, "memory_mib": 512},
        headers=auth_headers,
    )
    vm_id = create_resp.json()["id"]

    # First stop succeeds
    client.post(f"/vms/{vm_id}/stop", json={}, headers=auth_headers)

    # Second stop fails
    second_resp = client.post(f"/vms/{vm_id}/stop", json={}, headers=auth_headers)
    assert second_resp.status_code == 400
    assert "only ACTIVE VMs can be stopped" in second_resp.json()["message"]


def test_stop_identity_mismatch_marks_unknown(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Ensure if process identity does not match stored records,
    no destructive action is taken, and VM is marked UNKNOWN (Rule 12).
    """
    db_path = setup_test_environment["db_path"]

    create_resp = client.post(
        "/vms",
        json={"name": "tampered-vm", "owner_label": "qa", "vcpu": 1, "memory_mib": 512},
        headers=auth_headers,
    )
    vm_id = create_resp.json()["id"]
    host = create_resp.json()["host"]

    # Tamper with the worker's process table cmdline to simulate identity mismatch
    worker_state = (
        setup_test_environment["worker_1_state"]
        if host == "worker-1"
        else setup_test_environment["worker_2_state"]
    )
    pid = worker_state.vm_to_pid[vm_id]
    worker_state.processes[pid].cmdline = "tampered-process-command"

    # Attempt stop
    stop_resp = client.post(f"/vms/{vm_id}/stop", json={}, headers=auth_headers)
    assert stop_resp.status_code == 409
    data = stop_resp.json()
    assert data["error"] == "IDENTITY_VERIFICATION_FAILED"

    # VM must be transitioned to UNKNOWN
    vm_row = get_vm(db_path, vm_id)
    assert vm_row["label"] == "UNKNOWN"

    # Worker process must still exist (no destructive action taken)
    assert pid in worker_state.processes
