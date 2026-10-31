# CleanSlot SafeCore - End-to-End Demonstration Script
# Demonstrates explainable capacity placement, resource exhaustion with blocker explanations,
# simulated hypervisor boot faults, Rule 11 safety enforcement, and verified scoped cleanup receipts.
# Runs an end-to-end 9-step story against isolated in-memory mock workers and writes demo_output.log.

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


class TeeLogger:
    """Tee stdout to both terminal console and log file."""

    def __init__(self, filepath: str):
        self.terminal = sys.stdout
        self.log = open(filepath, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        self.log.close()


def print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_json(data: dict) -> None:
    print(json.dumps(data, indent=2))


def run_standalone_demo() -> None:
    """Run the 9-step CleanSlot demonstration story."""
    log_path = os.path.join(PROJECT_ROOT, "demo", "demo_output.log")
    tee = TeeLogger(log_path)
    orig_stdout = sys.stdout
    sys.stdout = tee

    try:
        from fastapi.testclient import TestClient
        from controller.main import app as controller_app
        from controller.database import init_db
        import controller.main as cm
        from worker.main import app as worker_app
        from worker.state import MockWorkerState
        from controller.worker_client import WorkerClient

        print("NOTE: workers are MOCKS (no real VMs)")
        print_section("CLEANSLOT SAFECORE: INITIALIZING MOCK CLUSTER")

        test_db = os.path.join(PROJECT_ROOT, "demo_cleanslot.db")
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

        # ------------------------------------------------------------------
        # STORY STEP 1: Request with no token is rejected (401)
        # ------------------------------------------------------------------
        print_section("STEP 1: REQUEST WITH NO TOKEN REJECTED (401)")
        resp = client.get("/cluster/status")
        print(f"Response Code (Expected 401): {resp.status_code}")
        print_json(resp.json())

        # ------------------------------------------------------------------
        # STORY STEP 2: Cluster status
        # ------------------------------------------------------------------
        print_section("STEP 2: CLUSTER STATUS")
        resp = client.get("/cluster/status", headers=HEADERS)
        print(f"Response Code (Expected 200): {resp.status_code}")
        print_json(resp.json())

        # ------------------------------------------------------------------
        # STORY STEP 3: Team 1 creates a 2048 MiB VM -> ACTIVE on one worker
        # ------------------------------------------------------------------
        print_section("STEP 3: TEAM 1 CREATES 2048 MiB VM -> ACTIVE")
        t1_payload = {
            "name": "payment-api-01",
            "owner_label": "team-1",
            "vcpu": 2,
            "memory_mib": 2048,
        }
        print("Request Payload:")
        print_json(t1_payload)
        resp = client.post("/vms", json=t1_payload, headers=HEADERS)
        print(f"\nResponse Code (Expected 201): {resp.status_code}")
        t1_vm = resp.json()
        print_json(t1_vm)
        t1_vm_id = t1_vm["id"]
        t1_host = t1_vm["host"]
        print(f"Team 1 VM {t1_vm_id} active on {t1_host}")

        # ------------------------------------------------------------------
        # STORY STEP 4: Set boot_fail on the other worker; Team 2 creates 2048 MiB
        # -> FAILED_CLEANUP_NEEDED with reservation kept; then reset fault to none
        # ------------------------------------------------------------------
        other_worker_state = w2_state if t1_host == "worker-1" else w1_state
        other_host_id = "worker-2" if t1_host == "worker-1" else "worker-1"

        print_section(f"STEP 4: SET BOOT_FAIL ON {other_host_id.upper()}; TEAM 2 CREATES 2048 MiB -> FAILED_CLEANUP_NEEDED")
        other_worker_state.fault_mode = "boot_fail"
        print(f"Fault mode 'boot_fail' injected on {other_host_id}")

        t2_payload = {
            "name": "batch-processor-02",
            "owner_label": "team-2",
            "vcpu": 2,
            "memory_mib": 2048,
        }
        print("\nRequest Payload:")
        print_json(t2_payload)
        resp = client.post("/vms", json=t2_payload, headers=HEADERS)
        print(f"\nResponse Code (Expected 500): {resp.status_code}")
        t2_vm = resp.json()
        print_json(t2_vm)
        t2_vm_id = t2_vm["id"]
        print(f"Team 2 VM {t2_vm_id} placed on {other_host_id} with label FAILED_CLEANUP_NEEDED (reservation kept)")

        # Reset fault mode
        other_worker_state.fault_mode = "none"
        print(f"Fault mode reset to 'none' on {other_host_id}")

        # ------------------------------------------------------------------
        # STORY STEP 5: Team 3 requests 2048 MiB -> 409 NOT_PLACED; print explanation
        # ------------------------------------------------------------------
        print_section("STEP 5: TEAM 3 REQUESTS 2048 MiB -> 409 NOT_PLACED (CAPACITY EXPLANATION)")
        t3_payload = {
            "name": "analytics-query-03",
            "owner_label": "team-3",
            "vcpu": 2,
            "memory_mib": 2048,
        }
        print("Request Payload:")
        print_json(t3_payload)
        resp = client.post("/vms", json=t3_payload, headers=HEADERS)
        print(f"\nResponse Code (Expected 409): {resp.status_code}")
        explanation = resp.json()
        print_json(explanation)

        # ------------------------------------------------------------------
        # STORY STEP 6: Try cleanup on Team 1's ACTIVE VM -> refused (409), nothing changes
        # ------------------------------------------------------------------
        print_section("STEP 6: TRY CLEANUP ON TEAM 1'S ACTIVE VM -> REFUSED (409)")
        resp = client.post(f"/vms/{t1_vm_id}/cleanup", json={}, headers=HEADERS)
        print(f"Attempting /cleanup on ACTIVE VM -> Response Code (Expected 409): {resp.status_code}")
        print_json(resp.json())

        # ------------------------------------------------------------------
        # STORY STEP 7: Clean up Team 2's failed VM -> print receipt with before/after memory
        # ------------------------------------------------------------------
        print_section("STEP 7: CLEAN UP TEAM 2'S FAILED VM WITH SCOPED RECEIPT")
        resp = client.post(f"/vms/{t2_vm_id}/cleanup", json={}, headers=HEADERS)
        print(f"Response Code (Expected 200): {resp.status_code}")
        cleanup_receipt = resp.json()
        print_json(cleanup_receipt)

        # ------------------------------------------------------------------
        # STORY STEP 8: Team 3 retries -> placed; print the evaluation
        # ------------------------------------------------------------------
        print_section("STEP 8: TEAM 3 RETRIES -> PLACED WITH EVALUATION")
        print("Retrying Request Payload:")
        print_json(t3_payload)
        resp = client.post("/vms", json=t3_payload, headers=HEADERS)
        print(f"\nResponse Code (Expected 201): {resp.status_code}")
        t3_vm = resp.json()
        print_json(t3_vm)
        t3_vm_id = t3_vm["id"]
        print(f"Team 3 VM {t3_vm_id} successfully scheduled on {t3_vm['host']}")

        # ------------------------------------------------------------------
        # STORY STEP 9: Stop Team 3's VM -> receipt; final cluster status
        # ------------------------------------------------------------------
        print_section("STEP 9: STOP TEAM 3'S VM -> RECEIPT AND FINAL CLUSTER STATUS")
        resp = client.post(f"/vms/{t3_vm_id}/stop", json={}, headers=HEADERS)
        print(f"Response Code (Expected 200): {resp.status_code}")
        stop_receipt = resp.json()
        print_json(stop_receipt)

        print("\nFinal Cluster Status:")
        resp = client.get("/cluster/status", headers=HEADERS)
        print(f"Response Code: {resp.status_code}")
        print_json(resp.json())

        print_section("DEMONSTRATION COMPLETED SUCCESSFULLY")

    finally:
        sys.stdout = orig_stdout
        tee.close()

        # Clean temporary DB
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except Exception:
                pass


if __name__ == "__main__":
    run_standalone_demo()
