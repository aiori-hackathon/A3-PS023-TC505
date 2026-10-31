# CleanSlot SafeCore - Scoped Cleanup Verification Test Suite
# Enforces Rule 11: direct cleanup on ACTIVE VMs is prohibited.
# Validates scoped cleanup of FAILED_CLEANUP_NEEDED VMs with 3-field identity checks.
# Confirms memory release, receipt schema, and transition to UNKNOWN upon mismatch.

from fastapi.testclient import TestClient
from controller.database import (
    insert_vm,
    get_vm,
    get_reserved_memory_by_host,
)


def test_active_vm_cleanup_forbidden(client: TestClient, auth_headers):
    """Rule 11: Never stop or clean an ACTIVE VM automatically via /cleanup."""
    create_resp = client.post(
        "/vms",
        json={"name": "active-test-vm", "owner_label": "prod", "vcpu": 1, "memory_mib": 512},
        headers=auth_headers,
    )
    vm_id = create_resp.json()["id"]

    # Calling /cleanup on an ACTIVE VM must fail with 409 (or 400)
    cleanup_resp = client.post(f"/vms/{vm_id}/cleanup", json={}, headers=auth_headers)
    assert cleanup_resp.status_code in (400, 409)
    data = cleanup_resp.json()
    assert data["error"] == "INVALID_STATE"
    assert any("Rule 11" in r for r in data.get("reasons", []))


def test_scoped_cleanup_of_failed_vm(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Test scoped cleanup of a VM in FAILED_CLEANUP_NEEDED state.
    Verifies 3-field identity, releases memory reservation, and returns exact receipt.
    """
    db_path = setup_test_environment["db_path"]
    w1_state = setup_test_environment["worker_1_state"]

    # Spawn an unrelated active VM on worker-1 to ensure scoped isolation
    unrelated_proc = w1_state.spawn_process("vm-unrelated", 1, 512, "other")

    # Spawn a failed VM process on worker-1
    failed_proc = w1_state.spawn_process(
        "vm-failed-target", 2, 1024, "dev", status="FAILED_BOOT"
    )

    # Register failed VM in controller SQLite database
    insert_vm(
        db_path=db_path,
        vm_id="vm-failed-target",
        name="failed-vm-1",
        owner_label="dev",
        vcpu=2,
        memory_mib=1024,
        host="worker-1",
        label="FAILED_CLEANUP_NEEDED",
        pid=failed_proc.pid,
        start_time=failed_proc.start_time,
        cmdline=failed_proc.cmdline,
    )

    reserved_before = get_reserved_memory_by_host(db_path, "worker-1")
    assert reserved_before >= 1024

    # Execute scoped cleanup
    resp = client.post("/vms/vm-failed-target/cleanup", json={}, headers=auth_headers)
    assert resp.status_code == 200
    receipt = resp.json()["receipt"]

    # Verify receipt fields (Correction 5)
    assert receipt["vm_id"] == "vm-failed-target"
    assert receipt["action"] == "CLEANUP"
    assert receipt["result"] == "SUCCESS"
    assert receipt["checks"]["pid_found"] is True
    assert receipt["checks"]["identity_matched"] is True
    assert receipt["checks"]["process_absent_after"] is True
    assert receipt["checks"]["record_removed"] is True
    assert receipt["reservation_released_mib"] == 1024

    # Verify target process removed from worker table
    assert failed_proc.pid not in w1_state.processes

    # Verify unrelated process remains completely unaffected
    assert unrelated_proc.pid in w1_state.processes

    # Verify database transition to STOPPED_CLEAN
    vm_row = get_vm(db_path, "vm-failed-target")
    assert vm_row["label"] == "STOPPED_CLEAN"

    # Verify reservation released
    reserved_after = get_reserved_memory_by_host(db_path, "worker-1")
    assert reserved_after == reserved_before - 1024


def test_cleanup_identity_mismatch_marks_unknown(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    If stored start_time, pid, or cmdline does not match on cleanup,
    destructive action is aborted and VM is marked UNKNOWN (Rule 12).
    """
    db_path = setup_test_environment["db_path"]
    w1_state = setup_test_environment["worker_1_state"]

    proc = w1_state.spawn_process("vm-mismatch-target", 1, 512, "test")

    # Insert into DB with altered start_time
    insert_vm(
        db_path=db_path,
        vm_id="vm-mismatch-target",
        name="mismatch-vm",
        owner_label="test",
        vcpu=1,
        memory_mib=512,
        host="worker-1",
        label="FAILED_CLEANUP_NEEDED",
        pid=proc.pid,
        start_time="2020-01-01T00:00:00Z",  # altered start_time
        cmdline=proc.cmdline,
    )

    resp = client.post("/vms/vm-mismatch-target/cleanup", json={}, headers=auth_headers)
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "IDENTITY_VERIFICATION_FAILED"

    # Worker process must not be removed
    assert proc.pid in w1_state.processes

    # DB label updated to UNKNOWN
    vm_row = get_vm(db_path, "vm-mismatch-target")
    assert vm_row["label"] == "UNKNOWN"
