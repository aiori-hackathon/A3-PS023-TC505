# CleanSlot SafeCore - End-to-End Demonstration Script
# Demonstrates explainable placement, bounds rejection, simulated hypervisor faults,
# and verified scoped cleanup with receipts adhering to PS-023 hackathon objectives.
# Executable directly as a standalone demonstration or against live endpoints.

import os
import sys
import json
import httpx
from datetime import datetime

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Set local token
os.environ["CLEANSLOT_TOKEN"] = os.environ.get(
    "CLEANSLOT_TOKEN", "cleanslot-demo-token"
)
TOKEN = os.environ["CLEANSLOT_TOKEN"]
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_json(data: dict) -> None:
    print(json.dumps(data, indent=2))


def run_standalone_demo() -> None:
    """Run full demonstration using in-memory ASGI test transport."""
    from fastapi.testclient import TestClient
    from controller.main import app as controller_app
    from controller.database import init_db
    import controller.main as cm
    from worker.main import app as worker_app
    from worker.state import MockWorkerState
    from controller.worker_client import WorkerClient

    print_section("CLEANSLOT SAFECORE: INITIALIZING MOCK CLUSTER")

    # Set temporary DB
    test_db = "demo_cleanslot.db"
    os.environ["CLEANSLOT_DB_PATH"] = test_db
    init_db(test_db)

    w1_state = MockWorkerState(worker_id="worker-1", total_memory_mib=4096)
    w2_state = MockWorkerState(worker_id="worker-2", total_memory_mib=4096)

    class WorkerRouter:
        def __init__(self, state):
            self.state = state

        async def __call__(self, scope, receive, send):
            import worker.main as wm

            orig = wm.worker_state
            try:
                wm.worker_state = self.state
                await worker_app(scope, receive, send)
            finally:
                wm.worker_state = orig

    transports = {
        "http://127.0.0.1:8101": httpx.ASGITransport(app=WorkerRouter(w1_state)),
        "http://127.0.0.1:8102": httpx.ASGITransport(app=WorkerRouter(w2_state)),
    }
    cm.worker_client = WorkerClient(token=TOKEN, transports=transports)
    client = TestClient(controller_app)

    # STEP 1: Cluster Status
    print_section("STEP 1: INITIAL CLUSTER STATUS")
    resp = client.get("/cluster/status", headers=HEADERS)
    print("Status Code:", resp.status_code)
    print_json(resp.json())

    # STEP 2: Create VM with Explainable Placement
    print_section("STEP 2: CREATE VM (EXPLAINABLE CAPACITY PLACEMENT)")
    create_payload = {
        "name": "payment-api-01",
        "owner_label": "checkout-team",
        "vcpu": 2,
        "memory_mib": 1024,
    }
    print("Request Payload:")
    print_json(create_payload)
    resp = client.post("/vms", json=create_payload, headers=HEADERS)
    print("\nResponse Code:", resp.status_code)
    created_vm = resp.json()
    print_json(created_vm)
    active_vm_id = created_vm["id"]

    # STEP 3: Enforce Resource Bounds
    print_section("STEP 3: RESOURCE BOUNDS VALIDATION (LIMIT VIOLATION)")
    invalid_payload = {
        "name": "oversized-vm",
        "owner_label": "bigdata",
        "vcpu": 4,
        "memory_mib": 4096,
    }
    print("Request Payload (Exceeds Limits: RAM > 2048, vCPU > 2):")
    print_json(invalid_payload)
    resp = client.post("/vms", json=invalid_payload, headers=HEADERS)
    print("\nResponse Code (Expected 422):", resp.status_code)
    print_json(resp.json())

    # STEP 4: Fault Injection & Failed Boot Handling
    print_section("STEP 4: SIMULATED BOOT FAULT INJECTION (WORKERS)")
    w1_state.fault_mode = "boot_fail"
    w2_state.fault_mode = "boot_fail"
    print("Injected fault mode: 'boot_fail' across mock workers")

    failing_payload = {
        "name": "worker-batch-task",
        "owner_label": "batch-team",
        "vcpu": 2,
        "memory_mib": 1024,
    }
    resp = client.post("/vms", json=failing_payload, headers=HEADERS)
    print("Response Code (Expected 500):", resp.status_code)
    failed_vm = resp.json()
    print_json(failed_vm)
    failed_vm_id = failed_vm["id"]

    # Reset fault mode
    w1_state.fault_mode = "none"
    w2_state.fault_mode = "none"

    # STEP 5: Rule 11 Safety Enforcement (No Direct Cleanup on ACTIVE)
    print_section("STEP 5: RULE 11 SAFETY CHECK (NO DIRECT CLEANUP ON ACTIVE VM)")
    resp = client.post(f"/vms/{active_vm_id}/cleanup", json={}, headers=HEADERS)
    print("Attempting /cleanup on ACTIVE VM -> Code (Expected 400):", resp.status_code)
    print_json(resp.json())

    # STEP 6: Scoped Cleanup of FAILED_CLEANUP_NEEDED VM
    print_section("STEP 6: SCOPED CLEANUP OF FAILED VM WITH RECEIPT")
    resp = client.post(f"/vms/{failed_vm_id}/cleanup", json={}, headers=HEADERS)
    print("Response Code (Expected 200):", resp.status_code)
    cleanup_receipt = resp.json()
    print_json(cleanup_receipt)

    # STEP 7: Graceful Stop with Verified Receipt
    print_section("STEP 7: GRACEFUL STOP OF ACTIVE VM WITH RECEIPT")
    resp = client.post(f"/vms/{active_vm_id}/stop", json={}, headers=HEADERS)
    print("Response Code (Expected 200):", resp.status_code)
    stop_receipt = resp.json()
    print_json(stop_receipt)

    # STEP 8: Final Cluster Status
    print_section("STEP 8: FINAL CLUSTER STATUS")
    resp = client.get("/cluster/status", headers=HEADERS)
    print("Response Code:", resp.status_code)
    print_json(resp.json())

    # Cleanup test db
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except Exception:
            pass

    print_section("DEMONSTRATION COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    run_standalone_demo()
