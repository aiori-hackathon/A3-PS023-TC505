# CleanSlot SafeCore - Central Controller Application
# Coordinates VM lifecycle, explainable capacity placement, and verified recovery receipts.
# Enforces resource bounds, transient CREATING event logging, and the four permanent VM labels.
# Exposes authenticated endpoints on 127.0.0.1:8000 for user and automated management.

import os
import json
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from controller.auth import verify_auth_token
from controller.models import (
    VMCreateRequest,
    VMCreateResponse,
    VMResponse,
    StopResponse,
    CleanupResponse,
    ClusterStatusResponse,
    HostStatus,
    HostEvaluation,
    Receipt,
    NotPlacedResponse,
    HostExplanation,
    BlockerInfo,
    RequestedVMInfo,
)
from controller.database import (
    init_db,
    insert_event,
    insert_vm,
    get_vm,
    list_vms,
    update_vm_label,
    get_reserved_memory_by_host,
    get_label_counts_by_host,
    get_blockers_by_host,
    events_has_foreign_key,
)
from controller.placement import (
    validate_limits,
    evaluate_placement,
    compute_safe_free,
    BUDGET_MIB,
    SAFETY_BUFFER_MIB,
)
from controller.worker_client import WorkerClient

app = FastAPI(title="CleanSlot SafeCore Controller", version="1.0.0")

WORKER_URLS = {
    "worker-1": os.environ.get("WORKER_1_URL", "http://127.0.0.1:8101"),
    "worker-2": os.environ.get("WORKER_2_URL", "http://127.0.0.1:8102"),
}
worker_client = WorkerClient()


@app.on_event("startup")
def on_startup() -> None:
    """Ensure database schema is initialized on startup."""
    init_db()


@app.post(
    "/vms",
    response_model=VMCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_auth_token)],
)
async def create_vm(req: VMCreateRequest) -> Any:
    """
    Create and place a new VM.
    Enforces resource limits, checks safe_free capacity on both workers,
    logs transient CREATING state, dispatches boot, and persists permanent label.
    """
    # 1. Enforce strict limits before contacting workers (Rule 9)
    valid, limit_reasons = validate_limits(req.vcpu, req.memory_mib)
    if not valid:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "LIMIT_VIOLATION",
                "message": "Resource request violates allowed limits",
                "reasons": limit_reasons,
            },
        )

    # 2. Query worker capacities
    hosts_capacity: List[Dict[str, Any]] = []
    reserved_by_host: Dict[str, int] = {}

    for host_id, url in WORKER_URLS.items():
        reserved = get_reserved_memory_by_host(None, host_id)
        reserved_by_host[host_id] = reserved
        try:
            cap = await worker_client.get_capacity(url)
            hosts_capacity.append(
                {
                    "worker_id": host_id,
                    "reachable": True,
                    "mem_available_mib": cap.get("mem_available_mib", 0),
                }
            )
        except Exception as e:
            # If a worker is unreachable, report 0 available and reachable=False
            hosts_capacity.append(
                {
                    "worker_id": host_id,
                    "reachable": False,
                    "mem_available_mib": 0,
                    "error": str(e),
                }
            )

    # 3. Evaluate placement logic (Rule 8)
    selected_host, evaluations = evaluate_placement(
        hosts_capacity=hosts_capacity,
        requested_memory_mib=req.memory_mib,
        reserved_by_host=reserved_by_host,
    )

    if not selected_host:
        # Record event for rejection without creating VM row or calling worker create
        has_fk = events_has_foreign_key(None)
        event_vm_id = None if has_fk else None
        insert_event(
            db_path=None,
            vm_id=event_vm_id,
            action="PLACEMENT",
            old_state=None,
            new_state="REJECTED",
            message=f"Placement rejected (NOT_PLACED): No worker has sufficient safe_free for VM '{req.name}' ({req.memory_mib} MiB, vcpu={req.vcpu}) requested by '{req.owner_label}'",
        )

        all_blockers: List[Dict[str, Any]] = []
        hosts_output: List[Dict[str, Any]] = []

        for ev in evaluations:
            host_id = ev["host"]
            is_reachable = ev.get("reachable", True)
            blockers = get_blockers_by_host(None, host_id)
            all_blockers.extend(blockers)
            hosts_output.append(
                {
                    "worker_id": host_id,
                    "reachable": is_reachable,
                    "safe_free_mib": ev["safe_free"],
                    "reason": ev["reason"],
                    "blockers": blockers,
                }
            )

        failed_blockers = [
            b["vm_id"]
            for b in all_blockers
            if b["label"] == "FAILED_CLEANUP_NEEDED"
        ]
        unknown_blockers = [
            b["vm_id"] for b in all_blockers if b["label"] == "UNKNOWN"
        ]

        if failed_blockers:
            next_step = f"Cleanup required for failed VM(s): {', '.join(failed_blockers)}. Run authorised cleanup and retry."
        elif unknown_blockers:
            next_step = f"VM(s) in UNKNOWN state require operator review: {', '.join(unknown_blockers)}. Nothing is cleaned automatically."
        elif all_blockers:
            next_step = "Every blocker is ACTIVE and protected. Wait for workloads to complete or request an explicit stop."
        else:
            next_step = "No candidate host has sufficient capacity. Increase cluster capacity or reduce requested resources."

        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "result": "NOT_PLACED",
                "requested": {
                    "name": req.name,
                    "owner_label": req.owner_label,
                    "memory_mib": req.memory_mib,
                    "vcpu": req.vcpu,
                },
                "rule": "safe_free = min(mem_available, budget - reserved) - safety_buffer",
                "hosts": hosts_output,
                "next_step": next_step,
            },
        )

    # 4. Generate VM ID and log transient CREATING event (Rule 10 & Correction 8)
    vm_id = f"vm-{uuid.uuid4()}"
    now_str = datetime.now(timezone.utc).isoformat()
    insert_event(
        db_path=None,
        vm_id=vm_id,
        action="CREATE",
        old_state=None,
        new_state="CREATING",
        message=f"Placement decided on {selected_host}; booting VM on worker",
        time_str=now_str,
    )

    # 5. Dispatch creation and boot to selected worker
    worker_url = WORKER_URLS[selected_host]
    status_code, worker_resp = await worker_client.create_vm(
        worker_url=worker_url,
        vm_id=vm_id,
        vcpu=req.vcpu,
        memory_mib=req.memory_mib,
        owner_label=req.owner_label,
    )

    # 6. Resolve permanent label
    if status_code in (200, 201) and worker_resp.get("status") == "RUNNING":
        insert_vm(
            db_path=None,
            vm_id=vm_id,
            name=req.name,
            owner_label=req.owner_label,
            vcpu=req.vcpu,
            memory_mib=req.memory_mib,
            host=selected_host,
            label="ACTIVE",
            pid=worker_resp.get("pid"),
            start_time=worker_resp.get("start_time"),
            cmdline=worker_resp.get("cmdline"),
            created_at=now_str,
        )
        insert_event(
            db_path=None,
            vm_id=vm_id,
            action="CREATE",
            old_state="CREATING",
            new_state="ACTIVE",
            message="Mock VM booted successfully",
            time_str=datetime.now(timezone.utc).isoformat(),
        )
        return VMCreateResponse(
            id=vm_id,
            name=req.name,
            owner_label=req.owner_label,
            vcpu=req.vcpu,
            memory_mib=req.memory_mib,
            host=selected_host,
            label="ACTIVE",
            pid=worker_resp.get("pid"),
            start_time=worker_resp.get("start_time"),
            cmdline=worker_resp.get("cmdline"),
            created_at=now_str,
            selected_host=selected_host,
            evaluation=[HostEvaluation(**ev) for ev in evaluations],
        )
    else:
        # Boot failed or error occurred
        insert_vm(
            db_path=None,
            vm_id=vm_id,
            name=req.name,
            owner_label=req.owner_label,
            vcpu=req.vcpu,
            memory_mib=req.memory_mib,
            host=selected_host,
            label="FAILED_CLEANUP_NEEDED",
            pid=worker_resp.get("pid"),
            start_time=worker_resp.get("start_time"),
            cmdline=worker_resp.get("cmdline"),
            created_at=now_str,
        )
        insert_event(
            db_path=None,
            vm_id=vm_id,
            action="CREATE",
            old_state="CREATING",
            new_state="FAILED_CLEANUP_NEEDED",
            message=f"Boot failed on {selected_host}: {worker_resp.get('message', 'Unknown failure')}",
            time_str=datetime.now(timezone.utc).isoformat(),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "id": vm_id,
                "host": selected_host,
                "label": "FAILED_CLEANUP_NEEDED",
                "error": worker_resp.get("error", "BOOT_FAILED"),
                "message": worker_resp.get(
                    "message", "VM boot sequence failed; cleanup required"
                ),
                "evaluation": evaluations,
            },
        )


@app.post(
    "/vms/{id}/stop",
    response_model=StopResponse,
    dependencies=[Depends(verify_auth_token)],
)
async def stop_vm(id: str) -> Any:
    """
    Gracefully stop an ACTIVE VM and obtain a verified cleanup receipt.
    Releases memory reservation only after worker confirms cleanup.
    Marks UNKNOWN if identity verification fails.
    """
    vm = get_vm(None, id)
    if not vm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"VM with id {id} not found",
        )

    if vm["label"] != "ACTIVE":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "INVALID_STATE",
                "message": f"Cannot stop VM with label '{vm['label']}'; only ACTIVE VMs can be stopped",
            },
        )

    host = vm["host"]
    worker_url = WORKER_URLS.get(host)
    if not worker_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unknown worker host '{host}'",
        )

    status_code, worker_resp = await worker_client.stop_vm(
        worker_url=worker_url,
        vm_id=id,
        expected_pid=vm["pid"],
        expected_start_time=vm["start_time"],
        expected_cmdline=vm["cmdline"],
    )

    receipt_data = worker_resp
    is_success = (
        status_code == 200
        and receipt_data.get("result") == "SUCCESS"
        and receipt_data.get("checks", {}).get("identity_matched") is True
    )

    if is_success:
        update_vm_label(None, id, "STOPPED_CLEAN")
        insert_event(
            db_path=None,
            vm_id=id,
            action="STOP",
            old_state="ACTIVE",
            new_state="STOPPED_CLEAN",
            message="Gracefully stopped and verified cleaned",
            receipt_json=json.dumps(receipt_data),
        )
        return StopResponse(receipt=Receipt(**receipt_data))
    else:
        # Identity verification failed or stop error -> Mark UNKNOWN (Rule 12)
        update_vm_label(None, id, "UNKNOWN")
        insert_event(
            db_path=None,
            vm_id=id,
            action="STOP",
            old_state="ACTIVE",
            new_state="UNKNOWN",
            message="Process identity verification failed; marked UNKNOWN to protect workloads",
            receipt_json=json.dumps(receipt_data) if isinstance(receipt_data, dict) else None,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "receipt": receipt_data,
                "error": "IDENTITY_VERIFICATION_FAILED",
                "message": "Process identity verification failed; destructive action aborted. VM marked UNKNOWN.",
            },
        )


@app.post(
    "/vms/{id}/cleanup",
    response_model=CleanupResponse,
    dependencies=[Depends(verify_auth_token)],
)
async def cleanup_vm(id: str) -> Any:
    """
    Scoped cleanup of a FAILED_CLEANUP_NEEDED VM.
    Rule 11: Direct cleanup is prohibited on ACTIVE VMs.
    Rule 12: Verifies stored pid, start_time, and cmdline before removing.
    """
    vm = get_vm(None, id)
    if not vm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"VM with id {id} not found",
        )

    # Rule 11 check: Never stop or clean an ACTIVE VM automatically
    if vm["label"] == "ACTIVE":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "INVALID_STATE",
                "message": "Cannot directly clean up an ACTIVE VM; use /vms/{id}/stop instead",
                "reasons": [
                    "Rule 11: Never stop or clean an ACTIVE VM automatically. Direct cleanup is only valid for FAILED_CLEANUP_NEEDED"
                ],
            },
        )

    if vm["label"] != "FAILED_CLEANUP_NEEDED":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "INVALID_STATE",
                "message": f"Cannot cleanup VM with label '{vm['label']}'; only FAILED_CLEANUP_NEEDED VMs can be cleaned",
            },
        )

    host = vm["host"]
    worker_url = WORKER_URLS.get(host)
    if not worker_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unknown worker host '{host}'",
        )

    status_code, worker_resp = await worker_client.cleanup_vm(
        worker_url=worker_url,
        vm_id=id,
        expected_pid=vm["pid"],
        expected_start_time=vm["start_time"],
        expected_cmdline=vm["cmdline"],
    )

    receipt_data = worker_resp
    is_success = (
        status_code == 200
        and receipt_data.get("result") == "SUCCESS"
        and receipt_data.get("checks", {}).get("identity_matched") is True
    )

    if is_success:
        update_vm_label(None, id, "STOPPED_CLEAN")
        insert_event(
            db_path=None,
            vm_id=id,
            action="CLEANUP",
            old_state="FAILED_CLEANUP_NEEDED",
            new_state="STOPPED_CLEAN",
            message="Scoped cleanup verified and completed",
            receipt_json=json.dumps(receipt_data),
        )
        return CleanupResponse(receipt=Receipt(**receipt_data))
    else:
        update_vm_label(None, id, "UNKNOWN")
        insert_event(
            db_path=None,
            vm_id=id,
            action="CLEANUP",
            old_state="FAILED_CLEANUP_NEEDED",
            new_state="UNKNOWN",
            message="Process identity verification failed during cleanup; marked UNKNOWN",
            receipt_json=json.dumps(receipt_data) if isinstance(receipt_data, dict) else None,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "receipt": receipt_data,
                "error": "IDENTITY_VERIFICATION_FAILED",
                "message": "Process identity verification failed during cleanup. Marked UNKNOWN.",
            },
        )


@app.get(
    "/vms",
    response_model=List[VMResponse],
    dependencies=[Depends(verify_auth_token)],
)
async def get_all_vms() -> List[VMResponse]:
    """List all VMs with label, host, memory, and owner."""
    rows = list_vms(None)
    return [
        VMResponse(
            id=r["id"],
            name=r["name"],
            owner_label=r["owner_label"],
            vcpu=r["vcpu"],
            memory_mib=r["memory_mib"],
            host=r["host"],
            label=r["label"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@app.get(
    "/cluster/status",
    response_model=ClusterStatusResponse,
    dependencies=[Depends(verify_auth_token)],
)
async def get_cluster_status() -> ClusterStatusResponse:
    """Return per-host mem_available, reserved, safe_free, and label counts."""
    host_statuses: List[HostStatus] = []

    for host_id, url in WORKER_URLS.items():
        reserved = get_reserved_memory_by_host(None, host_id)
        label_counts = get_label_counts_by_host(None, host_id)
        try:
            cap = await worker_client.get_capacity(url)
            mem_available = cap.get("mem_available_mib", 0)
        except Exception:
            mem_available = 0

        safe_free = compute_safe_free(
            mem_available_mib=mem_available,
            reserved_mib=reserved,
        )

        host_statuses.append(
            HostStatus(
                worker_id=host_id,
                mem_available=mem_available,
                reserved=reserved,
                safe_free=safe_free,
                labels=label_counts,
            )
        )

    return ClusterStatusResponse(hosts=host_statuses)
