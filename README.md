<!--
CleanSlot SafeCore - System Documentation and Usage Guide
Overview of the two-node mock VM controller, explainable capacity decisions,
honest implementation status, Windows PowerShell setup, demo instructions, and safety boundaries.
Adheres strictly to AIORI Hackathon 3 Problem Statement PS-023 specifications.
-->

# CleanSlot SafeCore (PS-023)

A lightweight two-node VM controller with explainable placement and verified scoped cleanup receipts.

---

## 1. What SafeCore Is and the Problem It Solves

CleanSlot SafeCore is a lightweight two-node VM controller built to solve unexplainable scheduling failures and dirty host state left behind by failed boots. Traditional hypervisor controllers often fail silently or return generic capacity errors when memory is tight, leaving ghost processes and stale reservations that block subsequent workloads. SafeCore provides transparent placement decisions using a deterministic safe-free memory formula that shows callers exactly which VMs on each worker are blocking placement and recommends safe actions. If a VM fails to boot, SafeCore holds the reservation and assigns a permanent `FAILED_CLEANUP_NEEDED` label rather than dropping state. Scoped cleanup verifies a 3-field process identity before freeing resources and returns an auditable receipt proving memory restoration without touching active VMs.

---

## 2. Honest Status

SafeCore is implemented as a **mock-first prototype** adhering to PS-023 specifications:

- **BUILT**:
  - Central controller with REST APIs on `127.0.0.1:8000`.
  - Explainable placement engine using $\text{safe\_free} = \min(\text{mem\_available}, \text{budget} - \text{reserved}) - \text{safety\_buffer}$.
  - Resource bounds validation (RAM 512–2048 MiB, vCPU 1–2) returning HTTP 422 before worker dispatch.
  - Transparent capacity rejection (`409 NOT_PLACED`) detailing blockers, safe actions, and next steps.
  - Four strict permanent labels (`ACTIVE`, `STOPPED_CLEAN`, `FAILED_CLEANUP_NEEDED`, `UNKNOWN`).
  - SQLite database storing VM records and auditable `events` history (with transient `CREATING` tracking).
  - Scoped verified cleanup with 3-field process identity checks (`pid`, `start_time`, `cmdline`) and structured receipts.
  - Rule 11 safety enforcement rejecting direct cleanup on `ACTIVE` VMs with HTTP 409.
  - Worker fault simulation endpoint supporting `boot_fail` and `none`.
  - Constant-time Bearer token authentication via `hmac.compare_digest`.
- **MOCKED**:
  - Worker agents run as mock services on `127.0.0.1:8101` and `127.0.0.1:8102`.
  - Process IDs are fake integers stored as keys in an in-memory Python dictionary.
  - Host memory allocation and release are simulated arithmetic values against configured budgets.
  - Hypervisor command lines and API socket paths are mock formatted strings.
- **NOT IMPLEMENTED**:
  - Real Cloud Hypervisor binary execution or IPC socket communication.
  - Linux KVM kernel virtualization (`/dev/kvm`).
  - Real OS-level process management or signal termination (`kill`, `SIGTERM`, `taskkill`).
  - Real hardware memory monitoring (`/proc/meminfo`, cgroups v2).
  - Multi-master controller replication or database clustering.

---

## 3. Setup and Run for Windows PowerShell

Open Windows PowerShell in the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> **Note**: Do not use `&&` in Windows PowerShell. Run each command on a separate line or separate with `;`.

---

## 4. Running Tests, Demos, and Live Services

### Run the Automated Pytest Suite
Runs all 29 unit and integration tests (including auth, limits, placement, fault injection, receipts, and chaos loops):

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Run the End-to-End Demonstration Story
Runs the 9-step demonstration story verifying token rejection, VM creation, fault injection, explainable capacity rejection, Rule 11 safety, and receipt generation.

```powershell
.\.venv\Scripts\python.exe demo\run_demo.py
```

> **Note**: `run_demo.py` uses an in-process FastAPI TestClient with ASGI transports; it does **not** start background operating system processes or require running live servers. Output is automatically saved to `demo/demo_output.log`.

### Run the Chaos-Lite Stress Loop
Runs 20 back-to-back deterministic stress cycles alternating between workers and fault modes, validating zero leaked fake processes and zero leaked memory reservations:

```powershell
.\.venv\Scripts\python.exe demo\chaos_lite.py
```
*(Results and a per-cycle execution table are written to `docs/chaos_result.txt`)*

### Starting the Three Live Services for Manual API Testing
To test the REST APIs using `Invoke-RestMethod` or `curl`:

1. **Set the Token**:
   ```powershell
   $env:CLEANSLOT_TOKEN = "cleanslot-secret-token"
   ```

2. **Terminal 1: Start Mock Worker 1 (Port 8101)**:
   ```powershell
   $env:WORKER_ID = "worker-1"
   $env:CLEANSLOT_TOKEN = "cleanslot-secret-token"
   .\.venv\Scripts\python.exe -m uvicorn worker.main:app --host 127.0.0.1 --port 8101
   ```

3. **Terminal 2: Start Mock Worker 2 (Port 8102)**:
   ```powershell
   $env:WORKER_ID = "worker-2"
   $env:CLEANSLOT_TOKEN = "cleanslot-secret-token"
   .\.venv\Scripts\python.exe -m uvicorn worker.main:app --host 127.0.0.1 --port 8102
   ```

4. **Terminal 3: Start Central Controller (Port 8000)**:
   ```powershell
   $env:CLEANSLOT_TOKEN = "cleanslot-secret-token"
   .\.venv\Scripts\python.exe -m uvicorn controller.main:app --host 127.0.0.1 --port 8000
   ```

---

## 5. The Novelty: Explainable Capacity Decisions (`409 NOT_PLACED`)

When a valid request cannot be scheduled, SafeCore does not simply say "no capacity". It returns a detailed breakdown of candidate hosts, the exact placement rule applied, each active workload blocking capacity, and specific recommended actions.

Real excerpt from `demo/demo_output.log` (Step 5):

```json
{
  "result": "NOT_PLACED",
  "requested": {
    "name": "analytics-query-03",
    "owner_label": "team-3",
    "memory_mib": 2048,
    "vcpu": 2
  },
  "rule": "safe_free = min(mem_available, budget - reserved) - safety_buffer",
  "hosts": [
    {
      "worker_id": "worker-1",
      "reachable": true,
      "safe_free_mib": 1280,
      "reason": "safe_free (1280 MiB) < requested (2048 MiB) [mem_available: 1536 MiB, budget: 4096 MiB, reserved: 2048 MiB, buffer: 256 MiB]",
      "blockers": [
        {
          "vm_id": "vm-75988e0c-20b3-49a9-b83b-b5f1a080c1d1",
          "owner_label": "team-1",
          "memory_mib": 2048,
          "label": "ACTIVE",
          "created_at": "2026-10-31T16:45:01.938366+00:00",
          "last_verified_at": "2026-10-31T16:45:01.938366+00:00",
          "safe_action": "Do not stop automatically. Only an explicit stop request may stop it."
        }
      ]
    },
    {
      "worker_id": "worker-2",
      "reachable": true,
      "safe_free_mib": 1280,
      "reason": "safe_free (1280 MiB) < requested (2048 MiB) [mem_available: 1536 MiB, budget: 4096 MiB, reserved: 2048 MiB, buffer: 256 MiB]",
      "blockers": [
        {
          "vm_id": "vm-e8786023-7207-4021-93b8-f158be22d2e2",
          "owner_label": "team-2",
          "memory_mib": 2048,
          "label": "FAILED_CLEANUP_NEEDED",
          "created_at": "2026-10-31T16:45:01.961734+00:00",
          "last_verified_at": "2026-10-31T16:45:01.961734+00:00",
          "safe_action": "Authorised cleanup recommended."
        }
      ]
    }
  ],
  "next_step": "Cleanup required for failed VM(s): vm-e8786023-7207-4021-93b8-f158be22d2e2. Run authorised cleanup and retry."
}
```

---

## 6. Safety Rules and Boundaries

- **Rule 1 & 2 (No OS Kills)**: Fake PIDs are strictly integer keys in Python dictionaries. No operating system commands (`os.kill`, `subprocess.kill`, `taskkill`, `pkill`) are ever executed.
- **Rule 11 (ACTIVE Protection)**: An `ACTIVE` VM is never stopped or cleaned automatically. Calling `/cleanup` on an active VM returns HTTP 409 and aborts.
- **Rule 12 (3-Field Identity Verification)**: Cleanup and stop require verification of stored `pid`, `start_time`, and `cmdline`. If verification fails, the VM is marked `UNKNOWN` and destructive cleanup is prevented.
- **Rule 10 (Strict Label Accounting)**: The SQLite database strictly restricts labels to `ACTIVE`, `STOPPED_CLEAN`, `FAILED_CLEANUP_NEEDED`, and `UNKNOWN`. Transient `CREATING` is recorded exclusively in the audit `events` table.
- **Localhost Isolation**: All servers bind strictly to `127.0.0.1`. No external ports or public network interfaces are exposed.
