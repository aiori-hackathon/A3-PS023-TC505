<!--
CleanSlot SafeCore - Architecture and Design Documentation
Documents the two-node controller architecture, placement formula, and state transitions.
Details 3-field process verification and standardized receipt structures for hackathon evaluation.
Maintained as technical reference for operators and evaluators.
-->

# CleanSlot SafeCore Architecture

CleanSlot SafeCore is an explainable capacity controller and verified cleanup system designed for two-node Cloud Hypervisor virtualization environments (Problem Statement PS-023).

## 1. System Overview

CleanSlot coordinates VM management across two worker nodes using an admission-controlled placement engine and verifiable scoped cleanup receipts:

```text
                  User / API Client
                         |  (Bearer Token Auth)
                         v
              +----------------------+
              |  CleanSlot Controller | (Port 8000)
              |  SQLite: vms, events |
              +----------+-----------+
                         |
           +-------------+-------------+
           |                           |
           v                           v
  +-----------------+         +-----------------+
  | Mock Worker 1   |         | Mock Worker 2   |
  | (Port 8101)     |         | (Port 8102)     |
  | Process Table   |         | Process Table   |
  | In-Memory State |         | In-Memory State |
  +-----------------+         +-----------------+
```

## 2. Key Design Principles

1. **Placement Formula (Rule 8)**:
   $$\text{safe\_free} = \min(\text{mem\_available}, \text{budget} - \text{reserved}) - \text{safety\_buffer}$$
   - A VM fits if $\text{safe\_free} \ge \text{requested}$.
   - The controller selects the eligible host with the highest $\text{safe\_free}$.
   - If no host fits, a `409 Conflict` is returned containing an `evaluation` list detailing each host's $\text{safe\_free}$, eligibility, and exact reason.

2. **Resource Boundaries (Rule 9)**:
   - RAM: 512 MiB to 2048 MiB.
   - vCPU: 1 to 2.
   - Requests outside these limits are rejected with `422 Unprocessable Entity` before contacting any worker.

3. **Lifecycle States & Invariant (Rule 10)**:
   - Exactly four permanent labels exist: `ACTIVE`, `STOPPED_CLEAN`, `FAILED_CLEANUP_NEEDED`, and `UNKNOWN`.
   - The transient `CREATING` state is recorded exclusively in the `events` table during in-flight dispatch to preserve the strict 4-label invariant in the `vms` table.

4. **Safety & Protection (Rules 11 & 12)**:
   - **Rule 11**: An `ACTIVE` VM is never automatically stopped or cleaned. Only an explicit `/stop` request can terminate it. Direct `/cleanup` requests on `ACTIVE` VMs are rejected.
   - **Rule 12**: Both `/stop` and `/cleanup` verify three identity fields:
     - Stored `pid`
     - Stored `start_time`
     - Stored `cmdline`
     If any check fails, destructive action is aborted, reservations are retained, and the VM transitions to `UNKNOWN`.

5. **Memory Accounting & Receipts (Rule 13 & Correction 5)**:
   - Worker available memory accounting subtracts all processes in the mock table, including those in `FAILED_BOOT` status.
   - Memory reservations are released only after worker confirmation.
   - All stop and cleanup actions issue a verified receipt containing exact audit details.
