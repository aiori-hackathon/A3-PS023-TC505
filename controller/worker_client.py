# CleanSlot SafeCore - Controller Worker HTTP Client
# Coordinates authenticated HTTP communication with worker agents on loopback.
# Transmits Bearer tokens and passes full identity tuples (pid, start_time, cmdline).
# Enables capacity queries, mock VM creation, graceful stop, and scoped cleanup.

import os
from typing import Dict, List, Tuple, Any, Optional
import httpx
from controller.auth import get_expected_token


class WorkerClient:
    def __init__(
        self,
        token: Optional[str] = None,
        transports: Optional[Dict[str, httpx.BaseTransport]] = None,
    ):
        self._token = token or get_expected_token()
        self._transports = transports or {}

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _get_client(self, worker_url: str) -> httpx.AsyncClient:
        transport = self._transports.get(worker_url)
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    async def get_capacity(self, worker_url: str) -> Dict[str, Any]:
        """Query worker host capacity metrics."""
        async with self._get_client(worker_url) as client:
            resp = await client.get(
                f"{worker_url}/internal/capacity", headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    async def create_vm(
        self,
        worker_url: str,
        vm_id: str,
        vcpu: int,
        memory_mib: int,
        owner_label: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Request creation and boot of a mock VM on the designated worker."""
        payload = {
            "vm_id": vm_id,
            "vcpu": vcpu,
            "memory_mib": memory_mib,
            "owner_label": owner_label,
        }
        async with self._get_client(worker_url) as client:
            resp = await client.post(
                f"{worker_url}/internal/vms",
                json=payload,
                headers=self._headers(),
            )
            try:
                data = resp.json()
            except Exception:
                data = {"raw_response": resp.text}
            return resp.status_code, data

    async def stop_vm(
        self,
        worker_url: str,
        vm_id: str,
        expected_pid: Optional[int],
        expected_start_time: Optional[str],
        expected_cmdline: Optional[str],
    ) -> Tuple[int, Dict[str, Any]]:
        """Request graceful stop with 3-field process identity verification."""
        payload = {
            "expected_pid": expected_pid,
            "expected_start_time": expected_start_time,
            "expected_cmdline": expected_cmdline,
        }
        async with self._get_client(worker_url) as client:
            resp = await client.post(
                f"{worker_url}/internal/vms/{vm_id}/stop",
                json=payload,
                headers=self._headers(),
            )
            try:
                data = resp.json()
            except Exception:
                data = {"raw_response": resp.text}
            return resp.status_code, data

    async def cleanup_vm(
        self,
        worker_url: str,
        vm_id: str,
        expected_pid: Optional[int],
        expected_start_time: Optional[str],
        expected_cmdline: Optional[str],
    ) -> Tuple[int, Dict[str, Any]]:
        """Request scoped cleanup of a failed VM with 3-field identity verification."""
        payload = {
            "expected_pid": expected_pid,
            "expected_start_time": expected_start_time,
            "expected_cmdline": expected_cmdline,
        }
        async with self._get_client(worker_url) as client:
            resp = await client.post(
                f"{worker_url}/internal/vms/{vm_id}/cleanup",
                json=payload,
                headers=self._headers(),
            )
            try:
                data = resp.json()
            except Exception:
                data = {"raw_response": resp.text}
            return resp.status_code, data

    async def list_vms(self, worker_url: str) -> List[Dict[str, Any]]:
        """List active processes running on the worker."""
        async with self._get_client(worker_url) as client:
            resp = await client.get(
                f"{worker_url}/internal/vms", headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()
