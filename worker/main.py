# CleanSlot SafeCore - Worker Agent Application
# Exposes authenticated internal endpoints for capacity queries and mock VM operations.
# Implements simulated hypervisor boot faults and 3-field process identity cleanup.
# Operates identically on ports 8101 and 8102 distinguished by WORKER_ID.

import os
from typing import List, Any
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from worker.auth import verify_worker_auth
from worker.state import MockWorkerState
from worker.models import (
    WorkerCapacityResponse,
    WorkerVMCreateRequest,
    WorkerVMCreateResponse,
    IdentityVerificationRequest,
    WorkerReceipt,
    WorkerProcessInfo,
    FaultRequest,
    FaultResponse,
)

app = FastAPI(title="CleanSlot SafeCore Mock Worker", version="1.0.0")

# Shared state instance for this worker process
worker_state = MockWorkerState(
    worker_id=os.environ.get("WORKER_ID", "worker-1"),
    total_memory_mib=int(os.environ.get("WORKER_TOTAL_MEM_MIB", "4096")),
)


@app.get(
    "/internal/capacity",
    response_model=WorkerCapacityResponse,
    dependencies=[Depends(verify_worker_auth)],
)
def get_capacity() -> WorkerCapacityResponse:
    """Return available memory and process metrics for this host."""
    total = worker_state.total_memory_mib
    avail = worker_state.mem_available_mib
    used = total - avail
    return WorkerCapacityResponse(
        worker_id=worker_state.worker_id,
        total_memory_mib=total,
        mem_available_mib=avail,
        mem_used_mib=used,
        process_count=len(worker_state.processes),
    )


@app.post(
    "/internal/vms",
    response_model=WorkerVMCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_worker_auth)],
)
def create_mock_vm(req: WorkerVMCreateRequest) -> Any:
    """
    Create and boot a mock Cloud Hypervisor process.
    If fault mode is 'boot_fail', simulates a boot error while retaining
    the mock process in FAILED_BOOT state to require cleanup.
    """
    if worker_state.fault_mode == "boot_fail":
        proc = worker_state.spawn_process(
            vm_id=req.vm_id,
            vcpu=req.vcpu,
            memory_mib=req.memory_mib,
            owner_label=req.owner_label,
            status="FAILED_BOOT",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=WorkerVMCreateResponse(
                status="FAILED_BOOT",
                vm_id=req.vm_id,
                pid=proc.pid,
                start_time=proc.start_time,
                cmdline=proc.cmdline,
                message="Fake process created in FAILED_BOOT state; requires cleanup",
                error="Simulated hypervisor boot failure (fault=boot_fail)",
            ).model_dump(),
        )

    proc = worker_state.spawn_process(
        vm_id=req.vm_id,
        vcpu=req.vcpu,
        memory_mib=req.memory_mib,
        owner_label=req.owner_label,
        status="RUNNING",
    )
    return WorkerVMCreateResponse(
        status="RUNNING",
        vm_id=req.vm_id,
        pid=proc.pid,
        start_time=proc.start_time,
        cmdline=proc.cmdline,
        message="Mock VM booted successfully",
    )


@app.post(
    "/internal/vms/{id}/stop",
    response_model=WorkerReceipt,
    dependencies=[Depends(verify_worker_auth)],
)
def stop_mock_vm(id: str, req: IdentityVerificationRequest) -> Any:
    """
    Gracefully stop a mock VM after verifying 3-field identity.
    Returns standardized audit receipt.
    """
    success, receipt = worker_state.execute_stop_or_cleanup(
        vm_id=id,
        action="STOP",
        expected_pid=req.expected_pid,
        expected_start_time=req.expected_start_time,
        expected_cmdline=req.expected_cmdline,
    )
    if success:
        return receipt
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=receipt.model_dump(),
    )


@app.post(
    "/internal/vms/{id}/cleanup",
    response_model=WorkerReceipt,
    dependencies=[Depends(verify_worker_auth)],
)
def cleanup_mock_vm(id: str, req: IdentityVerificationRequest) -> Any:
    """
    Scoped cleanup of a failed mock VM after verifying 3-field identity.
    Returns standardized audit receipt.
    """
    success, receipt = worker_state.execute_stop_or_cleanup(
        vm_id=id,
        action="CLEANUP",
        expected_pid=req.expected_pid,
        expected_start_time=req.expected_start_time,
        expected_cmdline=req.expected_cmdline,
    )
    if success:
        return receipt
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=receipt.model_dump(),
    )


@app.get(
    "/internal/vms",
    response_model=List[WorkerProcessInfo],
    dependencies=[Depends(verify_worker_auth)],
)
def list_mock_vms() -> List[WorkerProcessInfo]:
    """List all mock processes currently in the worker process table."""
    return [
        WorkerProcessInfo(
            vm_id=p.vm_id,
            pid=p.pid,
            start_time=p.start_time,
            cmdline=p.cmdline,
            memory_mib=p.memory_mib,
            vcpu=p.vcpu,
            owner_label=p.owner_label,
            status=p.status,
        )
        for p in worker_state.processes.values()
    ]


@app.post(
    "/internal/fault",
    response_model=FaultResponse,
    dependencies=[Depends(verify_worker_auth)],
)
def set_fault_mode(req: FaultRequest) -> FaultResponse:
    """Set the worker fault injection mode: 'none' or 'boot_fail'."""
    worker_state.fault_mode = req.fault
    return FaultResponse(
        worker_id=worker_state.worker_id,
        fault=req.fault,
        message=f"Fault mode set to {req.fault}",
    )
