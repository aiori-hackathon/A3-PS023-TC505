<!--
CleanSlot SafeCore - API Specification Documentation
Specifies all HTTP endpoints, request headers, JSON schemas, and response formats.
Documents controller public endpoints and internal worker agent interfaces.
Provided as technical reference for API clients and automated tests.
-->

# CleanSlot SafeCore API Specification

All endpoints require the HTTP header:
```http
Authorization: Bearer <CLEANSLOT_TOKEN>
```
Tokens are validated via `hmac.compare_digest` against `$env:CLEANSLOT_TOKEN`.

---

## 1. Controller Endpoints (Port 8000)

### `POST /vms`
Evaluates candidate worker nodes using the safe free memory formula:
$$\text{safe\_free} = \min(\text{mem\_available}, \text{budget} - \text{reserved}) - \text{safety\_buffer}$$
Selects the eligible host with the highest `safe_free`.

- **Request Body**:
  ```json
  {
    "name": "payment-api-01",
    "owner_label": "checkout-team",
    "vcpu": 2,
    "memory_mib": 1024
  }
  ```

- **Response (201 Created)**:
  ```json
  {
    "id": "vm-d9cc3970-f14f-48e5-bb9c-e4fe7818324c",
    "name": "payment-api-01",
    "owner_label": "checkout-team",
    "vcpu": 2,
    "memory_mib": 1024,
    "host": "worker-1",
    "label": "ACTIVE",
    "pid": 1001,
    "start_time": "2026-10-31T13:12:46.698087+00:00",
    "cmdline": "cloud-hypervisor --api-socket /tmp/ch-vm-d9cc3.sock --cpus boot=2 --memory size=1024M",
    "created_at": "2026-10-31T13:12:46.698087+00:00",
    "selected_host": "worker-1",
    "evaluation": [
      {
        "host": "worker-1",
        "safe_free": 3328,
        "eligible": true,
        "reason": "Host has sufficient safe_free memory (3328 MiB >= 1024 MiB)"
      },
      {
        "host": "worker-2",
        "safe_free": 3328,
        "eligible": true,
        "reason": "Host has sufficient safe_free memory (3328 MiB >= 1024 MiB)"
      }
    ]
  }
  ```

- **Rejection (409 Conflict - `NOT_PLACED`)**:
  Returned when the request is valid (within limits) but no worker has sufficient `safe_free` capacity.
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

- **Rejection (422 Unprocessable Content - `LIMIT_VIOLATION`)**:
  Returned before contacting any worker if RAM < 512 or > 2048, or vCPU < 1 or > 2.
  ```json
  {
    "error": "LIMIT_VIOLATION",
    "message": "Resource request violates allowed limits",
    "reasons": [
      "Memory 4096 MiB exceeds allowed range [512, 2048] MiB",
      "vCPU 4 exceeds allowed range [1, 2]"
    ]
  }
  ```

- **Boot Failure (500 Internal Server Error)**:
  Returned when simulated boot fails on worker (`boot_fail` fault injected). The VM transitions to `FAILED_CLEANUP_NEEDED` and keeps its memory reservation.

---

### `POST /vms/{id}/stop`
Gracefully stops an `ACTIVE` VM, verifies 3-field process identity (`pid`, `start_time`, `cmdline`), removes mock process record, releases reservation, transitions to `STOPPED_CLEAN`, and returns verified receipt.

- **Response (200 OK)**:
  ```json
  {
    "receipt": {
      "vm_id": "vm-d9cc3970-f14f-48e5-bb9c-e4fe7818324c",
      "action": "STOP",
      "result": "SUCCESS",
      "checks": {
        "pid_found": true,
        "identity_matched": true,
        "process_absent_after": true,
        "record_removed": true
      },
      "host_available_mib": {
        "before": 2560,
        "after": 3584
      },
      "reservation_released_mib": 1024
    }
  }
  ```

---

### `POST /vms/{id}/cleanup`
Executes scoped cleanup of a `FAILED_CLEANUP_NEEDED` VM.

- **Refusal on ACTIVE VM (409 Conflict - Rule 11 Enforcement)**:
  Direct cleanup is prohibited on `ACTIVE` VMs. Only explicit stop is permitted.
  ```json
  {
    "error": "INVALID_STATE",
    "message": "Cannot directly clean up an ACTIVE VM; use /vms/{id}/stop instead",
    "reasons": [
      "Rule 11: Never stop or clean an ACTIVE VM automatically. Direct cleanup is only valid for FAILED_CLEANUP_NEEDED"
    ]
  }
  ```

- **Successful Cleanup (200 OK)**:
  ```json
  {
    "receipt": {
      "vm_id": "vm-59924548-3b1c-4cb1-8145-b913578de503",
      "action": "CLEANUP",
      "result": "SUCCESS",
      "checks": {
        "pid_found": true,
        "identity_matched": true,
        "process_absent_after": true,
        "record_removed": true
      },
      "host_available_mib": {
        "before": 3072,
        "after": 3584
      },
      "reservation_released_mib": 512
    }
  }
  ```

---

### `GET /vms`
Lists all registered VMs in SQLite inventory.

- **Response (200 OK)**:
  ```json
  [
    {
      "id": "vm-d9cc3970-f14f-48e5-bb9c-e4fe7818324c",
      "name": "payment-api-01",
      "owner_label": "checkout-team",
      "vcpu": 2,
      "memory_mib": 1024,
      "host": "worker-1",
      "label": "STOPPED_CLEAN",
      "created_at": "2026-10-31T13:12:46.698087+00:00"
    }
  ]
  ```

---

### `GET /cluster/status`
Returns capacity, reservations, and state counts per worker node.

- **Response (200 OK)**:
  ```json
  {
    "hosts": [
      {
        "worker_id": "worker-1",
        "mem_available": 3584,
        "reserved": 0,
        "safe_free": 3328,
        "labels": {
          "ACTIVE": 0,
          "STOPPED_CLEAN": 1,
          "FAILED_CLEANUP_NEEDED": 0,
          "UNKNOWN": 0
        }
      },
      {
        "worker_id": "worker-2",
        "mem_available": 3584,
        "reserved": 0,
        "safe_free": 3328,
        "labels": {
          "ACTIVE": 0,
          "STOPPED_CLEAN": 0,
          "FAILED_CLEANUP_NEEDED": 0,
          "UNKNOWN": 0
        }
      }
    ]
  }
  ```

---

## 2. Worker Internal Endpoints (Ports 8101 & 8102)

- `GET /internal/capacity`: Returns total, available, and allocated host memory metrics.
- `POST /internal/vms`: Creates and boots mock hypervisor process.
- `POST /internal/vms/{id}/stop`: Gracefully stops mock process after checking `expected_pid`, `expected_start_time`, and `expected_cmdline`.
- `POST /internal/vms/{id}/cleanup`: Performs scoped cleanup after checking `expected_pid`, `expected_start_time`, and `expected_cmdline`.
- `GET /internal/vms`: Lists active mock processes in the worker process table.
- `POST /internal/fault`: Configures fault simulation (`{"fault": "none"}` or `{"fault": "boot_fail"}`).
