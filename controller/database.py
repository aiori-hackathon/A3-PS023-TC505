# CleanSlot SafeCore - Controller Database Module
# Manages SQLite storage for VM records and auditable lifecycle event history.
# Strictly enforces the four permanent VM labels and logs unconstrained CREATING events.
# Provides queries for reserved capacity accounting and state reconciliation.

import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any


def get_db_path(db_path: Optional[str] = None) -> str:
    return db_path or os.environ.get("CLEANSLOT_DB_PATH", "cleanslot.db")


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection to the SQLite database with row factory enabled."""
    target_path = get_db_path(db_path)
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite tables for VMs and lifecycle events."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_label TEXT NOT NULL,
                    vcpu INTEGER NOT NULL,
                    memory_mib INTEGER NOT NULL,
                    host TEXT NOT NULL,
                    label TEXT NOT NULL CHECK (label IN ('ACTIVE', 'STOPPED_CLEAN', 'FAILED_CLEANUP_NEEDED', 'UNKNOWN')),
                    pid INTEGER,
                    start_time TEXT,
                    cmdline TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    vm_id TEXT,
                    action TEXT NOT NULL,
                    old_state TEXT,
                    new_state TEXT NOT NULL,
                    message TEXT NOT NULL,
                    receipt_json TEXT
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vms_host_label ON vms(host, label);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_vm_id ON events(vm_id);"
            )
    finally:
        conn.close()


def events_has_foreign_key(db_path: Optional[str] = None) -> bool:
    """Check if the events table has a foreign key to vms."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("PRAGMA foreign_key_list(events)")
        fks = cursor.fetchall()
        for fk in fks:
            if fk["table"] == "vms" or fk["to"] == "id":
                return True
        return len(fks) > 0
    finally:
        conn.close()


def get_blockers_by_host(
    db_path: Optional[str], host: str
) -> List[Dict[str, Any]]:
    """
    Retrieve all VMs on a worker host that hold memory capacity.
    Includes labels: ACTIVE, FAILED_CLEANUP_NEEDED, UNKNOWN, CREATING.
    Returns list sorted by memory_mib descending (largest first).
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT id, owner_label, memory_mib, label, created_at, updated_at
            FROM vms
            WHERE host = ? AND label IN ('ACTIVE', 'FAILED_CLEANUP_NEEDED', 'UNKNOWN')
            ORDER BY memory_mib DESC
            """,
            (host,),
        )
        blockers: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            label = row["label"]
            if label == "ACTIVE":
                safe_action = (
                    "Do not stop automatically. Only an explicit stop request may stop it."
                )
            elif label == "FAILED_CLEANUP_NEEDED":
                safe_action = "Authorised cleanup recommended."
            elif label == "UNKNOWN":
                safe_action = (
                    "Needs operator review. Nothing is cleaned automatically."
                )
            else:
                safe_action = "Creation in progress; wait or investigate."

            blockers.append(
                {
                    "vm_id": row["id"],
                    "owner_label": row["owner_label"],
                    "memory_mib": row["memory_mib"],
                    "label": label,
                    "created_at": row["created_at"],
                    "last_verified_at": row["updated_at"] or row["created_at"],
                    "safe_action": safe_action,
                }
            )
        return blockers
    finally:
        conn.close()


def insert_event(
    db_path: Optional[str],
    vm_id: Optional[str],
    action: str,
    old_state: Optional[str],
    new_state: str,
    message: str,
    receipt_json: Optional[str] = None,
    time_str: Optional[str] = None,
) -> int:
    """Insert an auditable lifecycle event record."""
    conn = get_connection(db_path)
    current_time = time_str or datetime.now(timezone.utc).isoformat()
    try:
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO events (time, vm_id, action, old_state, new_state, message, receipt_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (current_time, vm_id, action, old_state, new_state, message, receipt_json),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Fallback if legacy events table has NOT NULL constraint on vm_id and no FK
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO events (time, vm_id, action, old_state, new_state, message, receipt_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (current_time, vm_id or "", action, old_state, new_state, message, receipt_json),
                )
                return cursor.lastrowid
    finally:
        conn.close()


def insert_vm(
    db_path: Optional[str],
    vm_id: str,
    name: str,
    owner_label: str,
    vcpu: int,
    memory_mib: int,
    host: str,
    label: str,
    pid: Optional[int],
    start_time: Optional[str],
    cmdline: Optional[str],
    created_at: Optional[str] = None,
) -> None:
    """Insert a new VM record into SQLite."""
    conn = get_connection(db_path)
    now_str = created_at or datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO vms (id, name, owner_label, vcpu, memory_mib, host, label, pid, start_time, cmdline, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vm_id,
                    name,
                    owner_label,
                    vcpu,
                    memory_mib,
                    host,
                    label,
                    pid,
                    start_time,
                    cmdline,
                    now_str,
                    now_str,
                ),
            )
    finally:
        conn.close()


def get_vm(db_path: Optional[str], vm_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a VM by its unique ID."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM vms WHERE id = ?", (vm_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_vms(db_path: Optional[str]) -> List[Dict[str, Any]]:
    """List all registered VMs."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT id, name, owner_label, vcpu, memory_mib, host, label, created_at FROM vms ORDER BY created_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_vm_label(
    db_path: Optional[str], vm_id: str, new_label: str
) -> None:
    """Update the permanent label of a VM."""
    conn = get_connection(db_path)
    now_str = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            conn.execute(
                "UPDATE vms SET label = ?, updated_at = ? WHERE id = ?",
                (new_label, now_str, vm_id),
            )
    finally:
        conn.close()


def get_reserved_memory_by_host(db_path: Optional[str], host: str) -> int:
    """
    Calculate total reserved memory for a host.
    Includes VMs labeled ACTIVE or FAILED_CLEANUP_NEEDED.
    Memory is released only when worker confirms cleanup (STOPPED_CLEAN).
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT COALESCE(SUM(memory_mib), 0) AS total_reserved
            FROM vms
            WHERE host = ? AND label IN ('ACTIVE', 'FAILED_CLEANUP_NEEDED')
            """,
            (host,),
        )
        row = cursor.fetchone()
        return int(row["total_reserved"]) if row else 0
    finally:
        conn.close()


def get_label_counts_by_host(
    db_path: Optional[str], host: str
) -> Dict[str, int]:
    """Return count of VMs for each of the four permanent labels on a host."""
    conn = get_connection(db_path)
    counts = {
        "ACTIVE": 0,
        "STOPPED_CLEAN": 0,
        "FAILED_CLEANUP_NEEDED": 0,
        "UNKNOWN": 0,
    }
    try:
        cursor = conn.execute(
            "SELECT label, COUNT(*) as cnt FROM vms WHERE host = ? GROUP BY label",
            (host,),
        )
        for row in cursor.fetchall():
            if row["label"] in counts:
                counts[row["label"]] = int(row["cnt"])
        return counts
    finally:
        conn.close()


def get_events(
    db_path: Optional[str], vm_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve audit events, optionally filtered by VM ID."""
    conn = get_connection(db_path)
    try:
        if vm_id:
            cursor = conn.execute(
                "SELECT * FROM events WHERE vm_id = ? ORDER BY id ASC", (vm_id,)
            )
        else:
            cursor = conn.execute("SELECT * FROM events ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
