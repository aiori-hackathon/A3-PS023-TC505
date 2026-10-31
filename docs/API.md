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

---

## 1. Controller Endpoints (Port 8000)

### `POST /vms`
Creates and boots a new VM on the host with highest `safe_free`.

- **Request**:
  ```json
  {
    "name": "web-service-1",
    "owner_label": "team-infra",
    "vcpu": 2,
    "memory_mib": 1024
  }
  ```
- **Response (201 Created)**:
  ```json
  {
    "id": "vm-550e8400-e29b-41d4-a716-446655440000",
    "name": "web-service-1",
    "owner_label": "team-infra",
    "vcpu": 2,
    "memory_mib": 1024,
    "host": "worker-1",
    "label": "ACTIVE",
    "pid": 1042,
    "start_time": "2026-10-31T09:20:00Z",
    "cmdline": "cloud-hypervisor --api-socket /tmp/ch-vm-550e.sock --cpus boot=2 --memory size=1024M",
    "created_at": "2026-10-31T09:20:00Z",
    "selected_host": "worker-1",
    "evaluation": [
      {
        "host": "worker-1",
        "safe_free": 2816,
        "eligible": true,
        "reason": "Host has sufficient safe_free memory (2816 MiB >= 1024 MiB)"
      },
      {
        "host": "worker-2",
        "safe_free": 1792,
        "eligible": true,
        "reason": "Host has sufficient safe_free memory (1792 MiB >= 1024 MiB)"
      }
    ]
  }
  ```

---

### `POST /vms/{id}/stop`
Gracefully stops an `ACTIVE` VM, verifies identity, and issues a verified cleanup receipt.

- **Response (200 OK)**:
  ```json
  {
    "receipt": {
      "vm_id": "vm-550e8400-e29b-41d4-a716-446655440000",
      "action": "STOP",
      "result": "SUCCESS",
      "checks": {
        "pid_found": true,
        "identity_matched": true,
        "process_absent_after": true,
        "record_removed": true
      },
      "host_available_mib": {
        "before": 3072,
        "after": 4096
      },
      "reservation_released_mib": 1024
    }
  }
  ```

---

### `POST /vms/{id}/cleanup`
Executes scoped cleanup of a `FAILED_CLEANUP_NEEDED` VM.

- **Response (200 OK)**:
  ```json
  {
    "receipt": {
      "vm_id": "vm-failed-boot-001",
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
        "after": 4096
      },
      "reservation_released_mib": 1024
    }
  }
  ```

---

### `GET /vms`
Lists all registered VMs.

- **Response (200 OK)**:
  ```json
  [
    {
      "id": "vm-550e8400-e29b-41d4-a716-446655440000",
      "name": "web-service-1",
      "owner_label": "team-infra",
      "vcpu": 2,
      "memory_mib": 1024,
      "host": "worker-1",
      "label": "ACTIVE",
      "created_at": "2026-10-31T09:20:00Z"
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
        "reserved": 1024,
        "safe_free": 2816,
        "labels": {
          "ACTIVE": 1,
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
- `POST /internal/vms/{id}/stop`: Gracefully stops mock process after checking `pid`, `start_time`, and `cmdline`.
- `POST /internal/vms/{id}/cleanup`: Performs scoped cleanup after checking `pid`, `start_time`, and `cmdline`.
- `GET /internal/vms`: Lists active mock processes in the worker process table.
- `POST /internal/fault`: Configures fault simulation (`"none"` or `"boot_fail"`).
