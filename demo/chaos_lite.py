# CleanSlot SafeCore - Chaos-Lite Deterministic Loop Demonstration
# Simulates repeated lifecycle churn, simulated hypervisor boot faults, and scoped recovery.
# NOTE: All processes and PIDs are MOCKS simulated in-memory; no real OS processes are created or killed.
# Validates that 20 back-to-back cycles leak 0 mock processes and 0 MiB of memory reservations.

import os
import sys
from typing import Dict, Any, Optional
import httpx
from fastapi.testclient import TestClient

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["CLEANSLOT_TOKEN"] = os.environ.get(
    "CLEANSLOT_TOKEN", "cleanslot-chaos-token"
)
TOKEN = os.environ["CLEANSLOT_TOKEN"]
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def run_chaos_loop(
    num_cycles: int = 20,
    output_file: Optional[str] = "docs/chaos_result.txt",
) -> Dict[str, Any]:
    """
    Execute deterministic chaos-lite stress loop across mock workers.
    NOTE: Processes and PIDs are MOCK dictionaries; no operating-system processes run.
    """
    from controller.main import app as controller_app
    from controller.database import init_db
    import controller.main as cm
    from worker.main import app as worker_app
    from worker.state import MockWorkerState
    from controller.worker_client import WorkerClient

    test_db = os.path.join(PROJECT_ROOT, "chaos_cleanslot.db")
    os.environ["CLEANSLOT_DB_PATH"] = test_db
    init_db(test_db)

    # Initialize isolated mock workers (4096 MiB total, 512 MiB base OS -> 3584 MiB starting available)
    w1_state = MockWorkerState(worker_id="worker-1", total_memory_mib=4096)
    w2_state = MockWorkerState(worker_id="worker-2", total_memory_mib=4096)

    class WorkerRouter:
        def __init__(self, state: MockWorkerState):
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
    controller_client = TestClient(controller_app)
    w1_client = TestClient(WorkerRouter(w1_state))
    w2_client = TestClient(WorkerRouter(w2_state))

    cycle_records = []
    total_leaked_procs = 0
    total_leaked_mib = 0

    try:
        for cycle_num in range(1, num_cycles + 1):
            # (a) Alternate target worker between worker-1 and worker-2 each cycle
            target_worker = "worker-1" if (cycle_num % 2 != 0) else "worker-2"
            target_state = w1_state if target_worker == "worker-1" else w2_state
            is_even = (cycle_num % 2 == 0)

            payload = {
                "name": f"chaos-vm-{cycle_num}",
                "owner_label": "chaos-test",
                "vcpu": 1,
                "memory_mib": 512,
            }

            if is_even:
                # (b) Even cycles: set boot_fail on that worker, create VM -> expect FAILED_CLEANUP_NEEDED
                # with reservation kept; reset fault to none; run cleanup -> expect receipt with all checks true
                mode_str = "boot_fail"
                target_state.fault_mode = "boot_fail"

                # Ensure placement selects target_worker deterministically
                if target_worker == "worker-2":
                    w1_state.base_os_used_mib = 1024

                create_resp = controller_client.post("/vms", json=payload, headers=HEADERS)

                if target_worker == "worker-2":
                    w1_state.base_os_used_mib = 512

                assert create_resp.status_code == 500, f"Cycle {cycle_num}: Expected 500 boot failure"
                vm_data = create_resp.json()
                assert vm_data["label"] == "FAILED_CLEANUP_NEEDED", f"Cycle {cycle_num}: Expected FAILED_CLEANUP_NEEDED"
                assert vm_data["host"] == target_worker, f"Cycle {cycle_num}: Expected host {target_worker}"
                vm_id = vm_data["id"]

                # Reset fault
                target_state.fault_mode = "none"

                # Scoped cleanup
                cleanup_resp = controller_client.post(f"/vms/{vm_id}/cleanup", json={}, headers=HEADERS)
                assert cleanup_resp.status_code == 200, f"Cycle {cycle_num}: Expected 200 cleanup"
                receipt = cleanup_resp.json()["receipt"]
                assert receipt["result"] == "SUCCESS"
                assert receipt["checks"]["pid_found"] is True
                assert receipt["checks"]["identity_matched"] is True
                assert receipt["checks"]["process_absent_after"] is True
                assert receipt["checks"]["record_removed"] is True
                assert receipt["reservation_released_mib"] == 512
                action_str = "CLEANUP"

            else:
                # (c) Odd cycles: create a VM normally -> ACTIVE; then explicit stop -> expect receipt with all checks true
                mode_str = "normal"
                target_state.fault_mode = "none"

                # Ensure placement selects target_worker deterministically
                if target_worker == "worker-2":
                    w1_state.base_os_used_mib = 1024

                create_resp = controller_client.post("/vms", json=payload, headers=HEADERS)

                if target_worker == "worker-2":
                    w1_state.base_os_used_mib = 512

                assert create_resp.status_code == 201, f"Cycle {cycle_num}: Expected 201 created"
                vm_data = create_resp.json()
                assert vm_data["label"] == "ACTIVE", f"Cycle {cycle_num}: Expected ACTIVE"
                assert vm_data["host"] == target_worker, f"Cycle {cycle_num}: Expected host {target_worker}"
                vm_id = vm_data["id"]

                # Explicit stop
                stop_resp = controller_client.post(f"/vms/{vm_id}/stop", json={}, headers=HEADERS)
                assert stop_resp.status_code == 200, f"Cycle {cycle_num}: Expected 200 stop"
                receipt = stop_resp.json()["receipt"]
                assert receipt["result"] == "SUCCESS"
                assert receipt["checks"]["pid_found"] is True
                assert receipt["checks"]["identity_matched"] is True
                assert receipt["checks"]["process_absent_after"] is True
                assert receipt["checks"]["record_removed"] is True
                assert receipt["reservation_released_mib"] == 512
                action_str = "STOP"

            # (d) After EVERY cycle assert: both workers' process tables are empty,
            # controller reserved memory is 0 on both workers, and mem_available equals starting value
            w1_procs = w1_client.get("/internal/vms", headers=HEADERS).json()
            w2_procs = w2_client.get("/internal/vms", headers=HEADERS).json()
            status_resp = controller_client.get("/cluster/status", headers=HEADERS).json()
            hosts_map = {h["worker_id"]: h for h in status_resp["hosts"]}

            leaked_p = len(w1_procs) + len(w2_procs)
            w1_leaked_m = hosts_map["worker-1"]["reserved"] + (3584 - hosts_map["worker-1"]["mem_available"])
            w2_leaked_m = hosts_map["worker-2"]["reserved"] + (3584 - hosts_map["worker-2"]["mem_available"])
            leaked_m = max(0, w1_leaked_m) + max(0, w2_leaked_m)

            assert len(w1_procs) == 0, f"Cycle {cycle_num}: Worker 1 process table leaked fake process"
            assert len(w2_procs) == 0, f"Cycle {cycle_num}: Worker 2 process table leaked fake process"
            assert hosts_map["worker-1"]["reserved"] == 0, f"Cycle {cycle_num}: Worker 1 leaked reservation"
            assert hosts_map["worker-2"]["reserved"] == 0, f"Cycle {cycle_num}: Worker 2 leaked reservation"
            assert hosts_map["worker-1"]["mem_available"] == 3584, f"Cycle {cycle_num}: Worker 1 available memory not restored"
            assert hosts_map["worker-2"]["mem_available"] == 3584, f"Cycle {cycle_num}: Worker 2 available memory not restored"

            total_leaked_procs += leaked_p
            total_leaked_mib += leaked_m

            cycle_records.append(
                {
                    "cycle": cycle_num,
                    "target": target_worker,
                    "mode": mode_str,
                    "action": action_str,
                    "receipt_checks": "All 4 True",
                    "leaked_procs": leaked_p,
                    "leaked_mib": leaked_m,
                }
            )

        # (e) Print one summary line at the end
        summary_line = (
            f"CHAOS-LITE (MOCK): {num_cycles} cycles, "
            f"{total_leaked_procs} leaked fake processes, "
            f"{total_leaked_mib} MiB leaked reservation"
        )
        print(summary_line)

        # (f) Write summary and per-cycle table to docs/chaos_result.txt
        if output_file:
            abs_out = os.path.join(PROJECT_ROOT, output_file)
            os.makedirs(os.path.dirname(abs_out), exist_ok=True)
            with open(abs_out, "w", encoding="utf-8") as f:
                f.write("CleanSlot SafeCore - Chaos-Lite Deterministic Stress Test Results\n")
                f.write("=" * 82 + "\n")
                f.write("NOTE: Workers and PIDs are MOCKS (no real OS processes created or killed).\n")
                f.write(f"Summary: {summary_line}\n\n")
                f.write("PER-CYCLE EXECUTION TABLE\n")
                f.write("-" * 82 + "\n")
                f.write(
                    f"{'Cycle':>5} | {'Target':>8} | {'Mode':>9} | {'Action':>8} | "
                    f"{'Receipt Checks':>14} | {'Leaked Procs':>12} | {'Leaked MiB':>10}\n"
                )
                f.write("-" * 82 + "\n")
                for r in cycle_records:
                    f.write(
                        f"{r['cycle']:>5} | {r['target']:>8} | {r['mode']:>9} | {r['action']:>8} | "
                        f"{r['receipt_checks']:>14} | {r['leaked_procs']:>12} | {r['leaked_mib']:>10}\n"
                    )
                f.write("-" * 82 + "\n")

        return {
            "cycles": num_cycles,
            "leaked_processes": total_leaked_procs,
            "leaked_mib": total_leaked_mib,
            "records": cycle_records,
            "summary": summary_line,
        }

    finally:
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except Exception:
                pass


if __name__ == "__main__":
    run_chaos_loop()
