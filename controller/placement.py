# CleanSlot SafeCore - Controller Placement Engine
# Evaluates host capacity using the safe_free = min(mem_available, budget - reserved) - buffer formula.
# Enforces resource limits (RAM 512-2048 MiB, vCPU 1-2) and selects eligible hosts with maximum safe_free.
# Generates detailed per-host evaluations explaining why placement decisions were made.

import os
from typing import Dict, List, Optional, Tuple, Any

BUDGET_MIB = int(os.environ.get("CLEANSLOT_BUDGET_MIB", "4096"))
SAFETY_BUFFER_MIB = int(os.environ.get("CLEANSLOT_SAFETY_BUFFER_MIB", "256"))
MIN_RAM_MIB = 512
MAX_RAM_MIB = 2048
MIN_VCPU = 1
MAX_VCPU = 2


def validate_limits(vcpu: int, memory_mib: int) -> Tuple[bool, List[str]]:
    """
    Validate requested resources against strict system bounds (Rule 9).
    RAM: 512-2048 MiB, vCPU: 1-2.
    Must reject before contacting any worker.
    """
    reasons: List[str] = []
    if memory_mib < MIN_RAM_MIB or memory_mib > MAX_RAM_MIB:
        reasons.append(
            f"Memory {memory_mib} MiB exceeds allowed range [{MIN_RAM_MIB}, {MAX_RAM_MIB}] MiB"
        )
    if vcpu < MIN_VCPU or vcpu > MAX_VCPU:
        reasons.append(f"vCPU {vcpu} exceeds allowed range [{MIN_VCPU}, {MAX_VCPU}]")

    return len(reasons) == 0, reasons


def compute_safe_free(
    mem_available_mib: int,
    reserved_mib: int,
    budget_mib: int = BUDGET_MIB,
    safety_buffer_mib: int = SAFETY_BUFFER_MIB,
) -> int:
    """
    Compute safe_free according to Rule 8:
    safe_free = min(mem_available, budget - reserved) - safety_buffer
    """
    effective_ceiling = min(mem_available_mib, budget_mib - reserved_mib)
    return effective_ceiling - safety_buffer_mib


def evaluate_placement(
    hosts_capacity: List[Dict[str, Any]],
    requested_memory_mib: int,
    reserved_by_host: Dict[str, int],
    budget_mib: int = BUDGET_MIB,
    safety_buffer_mib: int = SAFETY_BUFFER_MIB,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Evaluate all candidate hosts against requested memory.
    Returns:
      (selected_host_id, evaluation_list)
    Where evaluation_list contains: host, safe_free, eligible, reason.
    Selection picks eligible host with the most safe_free.
    """
    evaluations: List[Dict[str, Any]] = []
    eligible_hosts: List[Tuple[str, int]] = []

    for host_info in hosts_capacity:
        host_id = host_info["worker_id"]
        mem_available = host_info.get("mem_available_mib", 0)
        reserved = reserved_by_host.get(host_id, 0)

        safe_free = compute_safe_free(
            mem_available_mib=mem_available,
            reserved_mib=reserved,
            budget_mib=budget_mib,
            safety_buffer_mib=safety_buffer_mib,
        )

        eligible = safe_free >= requested_memory_mib
        if eligible:
            reason = f"Host has sufficient safe_free memory ({safe_free} MiB >= {requested_memory_mib} MiB)"
            eligible_hosts.append((host_id, safe_free))
        else:
            reason = (
                f"safe_free ({safe_free} MiB) < requested ({requested_memory_mib} MiB) "
                f"[mem_available: {mem_available} MiB, budget: {budget_mib} MiB, "
                f"reserved: {reserved} MiB, buffer: {safety_buffer_mib} MiB]"
            )

        evaluations.append(
            {
                "host": host_id,
                "safe_free": safe_free,
                "eligible": eligible,
                "reason": reason,
            }
        )

    # Sort eligible hosts by safe_free descending
    if eligible_hosts:
        eligible_hosts.sort(key=lambda x: x[1], reverse=True)
        selected_host = eligible_hosts[0][0]
    else:
        selected_host = None

    return selected_host, evaluations
