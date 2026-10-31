# CleanSlot SafeCore - Lifecycle and Cluster Status Test Suite
# Verifies successful VM provisioning, transient CREATING event logging, and ACTIVE state.
# Validates database persistence without foreign key violations during pre-boot events.
# Confirms cluster status endpoint aggregates per-host resources and label counts.

from fastapi.testclient import TestClient
from controller.database import get_events, get_vm


def test_vm_create_lifecycle_and_audit_log(
    client: TestClient, auth_headers, setup_test_environment
):
    """
    Test end-to-end VM creation.
    Confirms transient CREATING event logged in events before worker boot,
    and permanent ACTIVE label in vms table.
    """
    payload = {
        "name": "prod-api-1",
        "owner_label": "api-team",
        "vcpu": 2,
        "memory_mib": 1024,
    }
    resp = client.post("/vms", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    vm_id = data["id"]

    assert data["label"] == "ACTIVE"
    assert data["pid"] is not None
    assert data["start_time"] is not None
    assert "cloud-hypervisor" in data["cmdline"]

    # Verify database persistence
    db_path = setup_test_environment["db_path"]
    vm_row = get_vm(db_path, vm_id)
    assert vm_row is not None
    assert vm_row["label"] == "ACTIVE"

    # Verify audit event history: CREATING -> ACTIVE
    events = get_events(db_path, vm_id)
    assert len(events) >= 2
    assert events[0]["new_state"] == "CREATING"
    assert events[1]["new_state"] == "ACTIVE"


def test_get_vms_listing(client: TestClient, auth_headers):
    """Verify GET /vms returns all created VMs with owner, host, and memory."""
    payload = {
        "name": "list-test-vm",
        "owner_label": "test-team",
        "vcpu": 1,
        "memory_mib": 512,
    }
    create_resp = client.post("/vms", json=payload, headers=auth_headers)
    assert create_resp.status_code == 201

    list_resp = client.get("/vms", headers=auth_headers)
    assert list_resp.status_code == 200
    vms = list_resp.json()
    assert len(vms) >= 1
    target = next((v for v in vms if v["name"] == "list-test-vm"), None)
    assert target is not None
    assert target["label"] == "ACTIVE"
    assert target["memory_mib"] == 512
    assert target["owner_label"] == "test-team"


def test_cluster_status(client: TestClient, auth_headers):
    """Verify GET /cluster/status returns status for both workers."""
    # Create a VM
    payload = {
        "name": "cluster-vm",
        "owner_label": "infra",
        "vcpu": 1,
        "memory_mib": 1024,
    }
    client.post("/vms", json=payload, headers=auth_headers)

    resp = client.get("/cluster/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "hosts" in data
    assert len(data["hosts"]) == 2

    worker_ids = [h["worker_id"] for h in data["hosts"]]
    assert "worker-1" in worker_ids
    assert "worker-2" in worker_ids

    for host in data["hosts"]:
        assert "mem_available" in host
        assert "reserved" in host
        assert "safe_free" in host
        assert "labels" in host
        assert "ACTIVE" in host["labels"]
