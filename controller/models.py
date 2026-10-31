# CleanSlot SafeCore - Controller Data Models
# Defines Pydantic data schemas for controller requests, responses, and receipts.
# Enforces validation boundaries, resource limits, and audit receipt specifications.
# Used across controller endpoints for explainable capacity decisions.

from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class VMCreateRequest(BaseModel):
    name: str = Field(..., description="Human-readable name of the VM")
    owner_label: str = Field(..., description="Owner or team identifier")
    vcpu: int = Field(..., description="Number of virtual CPUs (1-2)")
    memory_mib: int = Field(..., description="Memory in MiB (512-2048)")


class HostEvaluation(BaseModel):
    host: str
    safe_free: int
    eligible: bool
    reason: str


class VMCreateResponse(BaseModel):
    id: str
    name: str
    owner_label: str
    vcpu: int
    memory_mib: int
    host: str
    label: str
    pid: Optional[int] = None
    start_time: Optional[str] = None
    cmdline: Optional[str] = None
    created_at: str
    selected_host: str
    evaluation: List[HostEvaluation]


class VMResponse(BaseModel):
    id: str
    name: str
    owner_label: str
    vcpu: int
    memory_mib: int
    host: str
    label: str
    created_at: str


class ReceiptChecks(BaseModel):
    pid_found: bool
    identity_matched: bool
    process_absent_after: bool
    record_removed: bool


class HostAvailableMib(BaseModel):
    before: int
    after: int


class Receipt(BaseModel):
    vm_id: str
    action: str
    result: str
    checks: ReceiptChecks
    host_available_mib: HostAvailableMib
    reservation_released_mib: int


class StopResponse(BaseModel):
    receipt: Receipt


class CleanupResponse(BaseModel):
    receipt: Receipt


class HostStatus(BaseModel):
    worker_id: str
    mem_available: int
    reserved: int
    safe_free: int
    labels: Dict[str, int]


class ClusterStatusResponse(BaseModel):
    hosts: List[HostStatus]
