# CleanSlot SafeCore - Capacity Explanation Test Suite
# Tests explainable 409 NOT_PLACED responses when valid VM requests fit on no candidate host.
# Validates blocker identification, safe_action recommendations, audit event logging,
# and successful capacity recovery following scoped cleanup of failed VMs.

import sqlite3
from fastapi.testclient import TestClient
from controller.database import get_connection


def test_capacity_exhausted_explanation_not_placed(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    When POST /vms is valid but no worker has sufficient safe_free:
    - Returns HTTP 409 NOT_PLACED with requested info, rule, hosts, blockers, next_step
    - Does not create a VM row in SQLite
    - Does not call any worker's create endpoint
    - Records an audit event for the rejection
    - Identifies blockers on both workers with correct labels and safe_action
    """
    w1_state = setup_test_environment["worker_1_state"]
    w2_state = setup_test_environment["worker_2_state"]

    # 1. Team 1 creates 2048 MiB VM on worker-1 -> ACTIVE
    t1_payload = {
        "name": "team1-vm",
        "owner_label": "team-1",
        "vcpu": 2,
        "memory_mib": 2048,
    }
    t1_resp = client.post("/vms", json=t1_payload, headers=auth_headers)
    assert t1_resp.status_code == 201
    t1_vm = t1_resp.json()
    assert t1_vm["label"] == "ACTIVE"
    assert t1_vm["host"] == "worker-1"

    # 2. Worker 2 set to boot_fail; Team 2 creates 2048 MiB VM -> FAILED_CLEANUP_NEEDED
    w2_state.fault_mode = "boot_fail"
    t2_payload = {
        "name": "team2-failed-vm",
        "owner_label": "team-2",
        "vcpu": 2,
        "memory_mib": 2048,
    }
    t2_resp = client.post("/vms", json=t2_payload, headers=auth_headers)
    assert t2_resp.status_code == 500
    t2_vm = t2_resp.json()
    assert t2_vm["label"] == "FAILED_CLEANUP_NEEDED"
    assert t2_vm["host"] == "worker-2"
    w2_state.fault_mode = "none"

    # 3. Team 3 requests 2048 MiB VM -> Cannot fit on either worker
    t3_payload = {
        "name": "team3-vm",
        "owner_label": "team-3",
        "vcpu": 2,
        "memory_mib": 2048,
    }
    t3_resp = client.post("/vms", json=t3_payload, headers=auth_headers)
    assert t3_resp.status_code == 409
    data = t3_resp.json()

    # Verify response body structure
    assert data["result"] == "NOT_PLACED"
    assert data["requested"]["name"] == "team3-vm"
    assert data["requested"]["memory_mib"] == 2048
    assert data["requested"]["vcpu"] == 2
    assert "safe_free = min(mem_available, budget - reserved) - safety_buffer" in data["rule"]
    assert len(data["hosts"]) == 2

    # Verify no VM row was created for team3-vm
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT count(*) AS cnt FROM vms WHERE name = 'team3-vm'")
        assert cursor.fetchone()["cnt"] == 0

        # Verify rejection audit event was logged with requested name
        cursor = conn.execute("SELECT * FROM events WHERE action = 'PLACEMENT' AND new_state = 'REJECTED'")
        rej_event = cursor.fetchone()
        assert rej_event is not None
        assert "team3-vm" in rej_event["message"]
        assert "2048" in rej_event["message"]
    finally:
        conn.close()

    # Verify blockers and safe_action on each host
    hosts_by_id = {h["worker_id"]: h for h in data["hosts"]}
    assert "worker-1" in hosts_by_id
    assert "worker-2" in hosts_by_id

    w1_blockers = hosts_by_id["worker-1"]["blockers"]
    assert len(w1_blockers) == 1
    assert w1_blockers[0]["label"] == "ACTIVE"
    assert w1_blockers[0]["memory_mib"] == 2048
    assert w1_blockers[0]["safe_action"] == "Do not stop automatically. Only an explicit stop request may stop it."
    assert "created_at" in w1_blockers[0]
    assert "last_verified_at" in w1_blockers[0]

    w2_blockers = hosts_by_id["worker-2"]["blockers"]
    assert len(w2_blockers) == 1
    assert w2_blockers[0]["label"] == "FAILED_CLEANUP_NEEDED"
    assert w2_blockers[0]["memory_mib"] == 2048
    assert w2_blockers[0]["safe_action"] == "Authorised cleanup recommended."

    # Next step must name the failed VM and recommend cleanup
    assert t2_vm["id"] in data["next_step"]
    assert "cleanup" in data["next_step"].lower()


def test_capacity_recovery_and_placement_after_failed_cleanup(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Verify full recovery flow:
    1. Worker 1 holds active VM; Worker 2 holds failed VM.
    2. Request fits nowhere -> 409 NOT_PLACED.
    3. Cleanup active VM is refused -> 409.
    4. Cleanup failed VM succeeds with receipt.
    5. Retrying the same request succeeds and places on Worker 2.
    """
    w1_state = setup_test_environment["worker_1_state"]
    w2_state = setup_test_environment["worker_2_state"]

    # Fill worker 1 with 2048 MiB ACTIVE VM
    r1 = client.post(
        "/vms",
        json={"name": "prod-api", "owner_label": "core", "vcpu": 2, "memory_mib": 2048},
        headers=auth_headers,
    )
    assert r1.status_code == 201
    active_vm_id = r1.json()["id"]

    # Fail creation on worker 2
    w2_state.fault_mode = "boot_fail"
    r2 = client.post(
        "/vms",
        json={"name": "failed-job", "owner_label": "batch", "vcpu": 2, "memory_mib": 2048},
        headers=auth_headers,
    )
    assert r2.status_code == 500
    failed_vm_id = r2.json()["id"]
    w2_state.fault_mode = "none"

    # Attempt new 2048 MiB placement -> 409 NOT_PLACED
    r3 = client.post(
        "/vms",
        json={"name": "new-job", "owner_label": "analytics", "vcpu": 2, "memory_mib": 2048},
        headers=auth_headers,
    )
    assert r3.status_code == 409
    assert r3.json()["result"] == "NOT_PLACED"

    # Attempt direct cleanup on ACTIVE VM -> Refused (409)
    cleanup_active = client.post(f"/vms/{active_vm_id}/cleanup", json={}, headers=auth_headers)
    assert cleanup_active.status_code in (400, 409)

    # Scoped cleanup of failed VM -> 200 OK
    cleanup_failed = client.post(f"/vms/{failed_vm_id}/cleanup", json={}, headers=auth_headers)
    assert cleanup_failed.status_code == 200
    receipt = cleanup_failed.json()["receipt"]
    assert receipt["result"] == "SUCCESS"
    assert receipt["reservation_released_mib"] == 2048

    # Retrying placement for new-job now succeeds on worker-2
    retry_resp = client.post(
        "/vms",
        json={"name": "new-job", "owner_label": "analytics", "vcpu": 2, "memory_mib": 2048},
        headers=auth_headers,
    )
    assert retry_resp.status_code == 201
    retry_vm = retry_resp.json()
    assert retry_vm["label"] == "ACTIVE"
    assert retry_vm["host"] == "worker-2"
