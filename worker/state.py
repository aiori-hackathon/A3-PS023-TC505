# CleanSlot SafeCore - Mock Worker In-Memory State
# Manages fake Cloud Hypervisor process table and host memory allocations.
# Enforces safe mock operations without OS-level kills and subtracts all active/failed processes.
# Implements 3-field identity verification and produces standardized audit receipts.

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, Literal, List
from worker.models import WorkerReceipt, ReceiptChecks, HostAvailableMib


@dataclass
class MockProcess:
    pid: int
    vm_id: str
    start_time: str
    cmdline: str
    vcpu: int
    memory_mib: int
    owner_label: str
    status: Literal["RUNNING", "FAILED_BOOT"]


class MockWorkerState:
    def __init__(self, worker_id: Optional[str] = None, total_memory_mib: int = 4096):
        self.worker_id: str = worker_id or os.environ.get("WORKER_ID", "worker-1")
        self.total_memory_mib: int = total_memory_mib
        self.base_os_used_mib: int = 512
        self.fault_mode: Literal["none", "boot_fail"] = "none"
        self.pid_counter: int = 1000

        # Fake process table: fake PID (int) -> MockProcess
        # Rule 2: Fake PIDs are ONLY keys in this dictionary; no OS kills are ever executed.
        self.processes: Dict[int, MockProcess] = {}
        self.vm_to_pid: Dict[str, int] = {}

    @property
    def mem_available_mib(self) -> int:
        """
        Available memory on the mock host.
        Rule / Correction 3: Must subtract the memory of ALL VMs whose fake process
        still exists in the process table, including VMs in FAILED_BOOT state.
        """
        allocated = sum(p.memory_mib for p in self.processes.values())
        return max(0, self.total_memory_mib - self.base_os_used_mib - allocated)

    def spawn_process(
        self,
        vm_id: str,
        vcpu: int,
        memory_mib: int,
        owner_label: str,
        status: Literal["RUNNING", "FAILED_BOOT"] = "RUNNING",
    ) -> MockProcess:
        """Spawn a new fake Cloud Hypervisor process."""
        self.pid_counter += 1
        pid = self.pid_counter
        start_time = datetime.now(timezone.utc).isoformat()
        short_id = vm_id[:8] if len(vm_id) >= 8 else vm_id
        cmdline = f"cloud-hypervisor --api-socket /tmp/ch-{short_id}.sock --cpus boot={vcpu} --memory size={memory_mib}M"

        proc = MockProcess(
            pid=pid,
            vm_id=vm_id,
            start_time=start_time,
            cmdline=cmdline,
            vcpu=vcpu,
            memory_mib=memory_mib,
            owner_label=owner_label,
            status=status,
        )
        self.processes[pid] = proc
        self.vm_to_pid[vm_id] = pid
        return proc

    def execute_stop_or_cleanup(
        self,
        vm_id: str,
        action: str,
        expected_pid: Optional[int],
        expected_start_time: Optional[str],
        expected_cmdline: Optional[str],
    ) -> Tuple[bool, WorkerReceipt]:
        """
        Execute graceful stop or cleanup with 3-field process identity verification.
        Checks stored pid, start_time, and cmdline (Correction 4).
        Produces receipt with exact fields (Correction 5).
        """
        before_avail = self.mem_available_mib

        # Determine target PID
        pid = expected_pid if expected_pid is not None else self.vm_to_pid.get(vm_id)
        pid_found = pid is not None and pid in self.processes

        identity_matched = False
        freed_mib = 0

        if pid_found and pid is not None:
            proc = self.processes[pid]
            # Strict verification of all 3 identity values
            identity_matched = (
                proc.pid == expected_pid
                and proc.start_time == expected_start_time
                and proc.cmdline == expected_cmdline
                and proc.vm_id == vm_id
            )

            if identity_matched:
                freed_mib = proc.memory_mib
                # Remove only from dictionary (Rule 2)
                del self.processes[pid]
                if vm_id in self.vm_to_pid:
                    del self.vm_to_pid[vm_id]

        after_avail = self.mem_available_mib

        if identity_matched:
            receipt = WorkerReceipt(
                vm_id=vm_id,
                action=action,
                result="SUCCESS",
                checks=ReceiptChecks(
                    pid_found=True,
                    identity_matched=True,
                    process_absent_after=True,
                    record_removed=True,
                ),
                host_available_mib=HostAvailableMib(
                    before=before_avail, after=after_avail
                ),
                reservation_released_mib=freed_mib,
            )
            return True, receipt
        else:
            receipt = WorkerReceipt(
                vm_id=vm_id,
                action=action,
                result="FAILED",
                checks=ReceiptChecks(
                    pid_found=pid_found,
                    identity_matched=False,
                    process_absent_after=False,
                    record_removed=False,
                ),
                host_available_mib=HostAvailableMib(
                    before=before_avail, after=before_avail
                ),
                reservation_released_mib=0,
            )
            return False, receipt
