# CleanSlot SafeCore - Authentication Test Suite
# Verifies constant-time Bearer token authentication on controller and worker endpoints.
# Validates that missing, malformed, or incorrect tokens return HTTP 401 Unauthorized.
# Confirms that requests with valid tokens succeed without leaking secret values.

from fastapi.testclient import TestClient


def test_controller_auth_missing_token(client: TestClient):
    """Ensure missing token returns 401 on controller endpoints."""
    resp = client.get("/vms")
    assert resp.status_code == 401
    assert "Missing Authorization header" in resp.json()["detail"]


def test_controller_auth_malformed_header(client: TestClient):
    """Ensure non-Bearer or malformed header returns 401."""
    resp = client.get("/vms", headers={"Authorization": "Basic 12345"})
    assert resp.status_code == 401
    assert "Invalid Authorization header format" in resp.json()["detail"]


def test_controller_auth_invalid_token(client: TestClient, bad_auth_headers):
    """Ensure invalid token returns 401."""
    resp = client.get("/vms", headers=bad_auth_headers)
    assert resp.status_code == 401
    assert "Invalid or expired token" in resp.json()["detail"]


def test_controller_auth_valid_token(client: TestClient, auth_headers):
    """Ensure valid token is accepted."""
    resp = client.get("/vms", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_worker_auth_missing_token(worker_client_test: TestClient):
    """Ensure missing token returns 401 on worker internal endpoints."""
    resp = worker_client_test.get("/internal/capacity")
    assert resp.status_code == 401
    assert "Missing Authorization header" in resp.json()["detail"]


def test_worker_auth_invalid_token(worker_client_test: TestClient, bad_auth_headers):
    """Ensure invalid token returns 401 on worker internal endpoints."""
    resp = worker_client_test.get("/internal/capacity", headers=bad_auth_headers)
    assert resp.status_code == 401


def test_worker_auth_valid_token(worker_client_test: TestClient, auth_headers):
    """Ensure valid token returns 200 on worker internal capacity endpoint."""
    resp = worker_client_test.get("/internal/capacity", headers=auth_headers)
    assert resp.status_code == 200
    assert "mem_available_mib" in resp.json()
