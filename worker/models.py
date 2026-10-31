# CleanSlot SafeCore - Worker Data Models
# Defines Pydantic data schemas for internal mock worker APIs and receipt contracts.
# Specifies 3-field process identity requests and strictly formatted cleanup receipts.
# Used by worker services on ports 8101 and 8102.

from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class WorkerVMCreateRequest(BaseModel):
    vm_id: str
    vcpu: int
    memory_mib: int
    owner_label: str


class WorkerVMCreateResponse(BaseModel):
    status: str
    vm_id: str
    pid: int
    start_time: str
    cmdline: str
    message: str
    error: Optional[str] = None


class IdentityVerificationRequest(BaseModel):
    expected_pid: Optional[int] = None
    expected_start_time: Optional[str] = None
    expected_cmdline: Optional[str] = None


class ReceiptChecks(BaseModel):
    pid_found: bool
    identity_matched: bool
    process_absent_after: bool
    record_removed: bool


class HostAvailableMib(BaseModel):
    before: int
    after: int


class WorkerReceipt(BaseModel):
    vm_id: str
    action: str
    result: str
    checks: ReceiptChecks
    host_available_mib: HostAvailableMib
    reservation_released_mib: int


class WorkerCapacityResponse(BaseModel):
    worker_id: str
    total_memory_mib: int
    mem_available_mib: int
    mem_used_mib: int
    process_count: int


class WorkerProcessInfo(BaseModel):
    vm_id: str
    pid: int
    start_time: str
    cmdline: str
    memory_mib: int
    vcpu: int
    owner_label: str
    status: str


class FaultRequest(BaseModel):
    fault: Literal["none", "boot_fail"] = Field(
        ..., description="Fault mode: 'none' or 'boot_fail'"
    )


class FaultResponse(BaseModel):
    worker_id: str
    fault: str
    message: str
