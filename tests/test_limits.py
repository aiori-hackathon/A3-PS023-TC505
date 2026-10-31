# CleanSlot SafeCore - Resource Limits Test Suite
# Tests pre-flight validation of VM requests against bounds (RAM 512-2048 MiB, vCPU 1-2).
# Ensures requests violating limits are rejected immediately with HTTP 422 before worker contact.
# Validates clear explainable error messages returned in the response.

from fastapi.testclient import TestClient


def test_reject_ram_below_minimum(client: TestClient, auth_headers):
    """Ensure RAM below 512 MiB is rejected with 422."""
    payload = {
        "name": "too-small-vm",
        "owner_label": "dev",
        "vcpu": 1,
        "memory_mib": 256,
    }
    resp = client.post("/vms", json=payload, headers=auth_headers)
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"] == "LIMIT_VIOLATION"
    assert any("Memory 256 MiB exceeds allowed range" in r for r in data["reasons"])


def test_reject_ram_above_maximum(client: TestClient, auth_headers):
    """Ensure RAM above 2048 MiB is rejected with 422."""
    payload = {
        "name": "too-large-vm",
        "owner_label": "dev",
        "vcpu": 2,
        "memory_mib": 4096,
    }
    resp = client.post("/vms", json=payload, headers=auth_headers)
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"] == "LIMIT_VIOLATION"
    assert any("Memory 4096 MiB exceeds allowed range" in r for r in data["reasons"])


def test_reject_vcpu_out_of_bounds(client: TestClient, auth_headers):
    """Ensure vCPU < 1 or > 2 is rejected with 422."""
    payload = {
        "name": "bad-vcpu-vm",
        "owner_label": "dev",
        "vcpu": 4,
        "memory_mib": 1024,
    }
    resp = client.post("/vms", json=payload, headers=auth_headers)
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"] == "LIMIT_VIOLATION"
    assert any("vCPU 4 exceeds allowed range" in r for r in data["reasons"])


def test_reject_multiple_limit_violations(client: TestClient, auth_headers):
    """Ensure simultaneous RAM and vCPU violations report all reasons."""
    payload = {
        "name": "all-invalid-vm",
        "owner_label": "dev",
        "vcpu": 0,
        "memory_mib": 128,
    }
    resp = client.post("/vms", json=payload, headers=auth_headers)
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"] == "LIMIT_VIOLATION"
    assert len(data["reasons"]) >= 2
