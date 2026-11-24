"""OpenSpec Kanban enforcement DDL, validator hooks, and QA remediation helpers."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid


def run_openspec_migration(conn: sqlite3.Connection, *, commit: bool = True) -> None:
    """Create SQLite DDL for OpenSpec enforcement tables, columns, indexes, and triggers."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS openspec_enforcement_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at REAL NOT NULL DEFAULT (julianday('now'))
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS openspec_registry (
            id TEXT PRIMARY KEY,
            openspec_contract TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (julianday('now'))
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_run_identity_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES task_runs(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            identity_snapshot TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (julianday('now'))
        )
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_openspec_registry_update
        BEFORE UPDATE ON openspec_registry
        BEGIN
            SELECT RAISE(ABORT, 'SQLITE_CONSTRAINT_TRIGGER');
        END;
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_openspec_registry_delete
        BEFORE DELETE ON openspec_registry
        BEGIN
            SELECT RAISE(ABORT, 'SQLITE_CONSTRAINT_TRIGGER');
        END;
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_identity_events_update
        BEFORE UPDATE ON task_run_identity_events
        BEGIN
            SELECT RAISE(ABORT, 'SQLITE_CONSTRAINT_TRIGGER');
        END;
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_identity_events_delete
        BEFORE DELETE ON task_run_identity_events
        BEGIN
            SELECT RAISE(ABORT, 'SQLITE_CONSTRAINT_TRIGGER');
        END;
    """)

    for col, col_def in [
        ("openspec_contract", "TEXT"),
        ("openspec_contract_hash", "TEXT"),
        ("openspec_signature", "TEXT"),
        ("review_cycles", "INTEGER DEFAULT 0"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    try:
        cursor.execute("ALTER TABLE task_events ADD COLUMN correlation_id TEXT")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_events_correlation
        ON task_events(correlation_id)
        WHERE correlation_id IS NOT NULL
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_active_openspec_contract_update
        BEFORE UPDATE OF openspec_contract, openspec_contract_hash, openspec_signature ON tasks
        WHEN EXISTS (SELECT 1 FROM openspec_enforcement_meta WHERE key='enforcement_status' AND value='active')
          AND OLD.openspec_contract_hash IS NOT NULL
          AND (NEW.openspec_contract IS NOT OLD.openspec_contract
               OR NEW.openspec_contract_hash IS NOT OLD.openspec_contract_hash
               OR NEW.openspec_signature IS NOT OLD.openspec_signature)
        BEGIN
            SELECT RAISE(ABORT, 'OpenSpec contract is immutable while enforcement is active');
        END;
    """)
    if commit:
        conn.commit()


def record_fallback_event_on_conn(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    task_id: str,
    run_id: str,
    model: str,
    provider: str,
    reason: str,
) -> None:
    """Record a model/provider fallback event into ``task_run_identity_events``."""
    del session_id
    evt_id = f"evt_{uuid.uuid4().hex[:8]}"
    identity_snapshot = json.dumps({
        "effective_model": model,
        "effective_provider": provider,
        "reason": reason,
    })
    conn.execute(
        "INSERT INTO task_run_identity_events (id, run_id, task_id, identity_snapshot, event_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (evt_id, run_id, task_id, identity_snapshot, "fallback_activated", time.time()),
    )


def enforce_openspec_transaction_on_conn(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    new_status: str,
    expected_contract_hash: str | None,
) -> bool:
    """Enforce pre-transaction OpenSpec contract hash validation for task status transitions."""
    cursor = conn.execute("SELECT value FROM openspec_enforcement_meta WHERE key = 'enforcement_status'")
    row = cursor.fetchone()
    if not row or row[0] != "active":
        return True

    cursor = conn.execute(
        "SELECT openspec_contract_hash, status FROM tasks WHERE id = ?",
        (task_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Task {task_id} not found")

    contract_hash, current_status = row[0], row[1]
    if contract_hash != expected_contract_hash:
        raise ValueError(
            f"OpenSpec contract hash mismatch. Expected {expected_contract_hash}, got {contract_hash}"
        )

    cursor = conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ? AND status = ? AND openspec_contract_hash IS ?",
        (new_status, task_id, current_status, expected_contract_hash),
    )
    if cursor.rowcount != 1:
        raise ValueError(
            f"Concurrency conflict or invariant violation during status update for task {task_id}"
        )
    return True


def remediate_qa_failure(db: sqlite3.Connection, task_id: str) -> None:
    """Transition ``done``/``blocked`` task back to ``todo`` and append ``qa_remediation_initiated``."""
    cursor = db.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'review_requested' ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"No review_requested event found for task {task_id}")

    payload = json.loads(row[0])
    provenance_run_id = payload.get("provenance_run_id")
    provenance_event_id = payload.get("provenance_event_id")

    cursor = db.execute(
        "UPDATE tasks SET status = 'todo' WHERE id = ? AND status IN ('done', 'blocked')",
        (task_id,),
    )
    if cursor.rowcount != 1:
        raise ValueError(
            f"Expected to update 1 row, but updated {cursor.rowcount}. "
            f"Task {task_id} may not exist or not be in 'done' or 'blocked' state."
        )

    event_payload = json.dumps({
        "provenance_run_id": provenance_run_id,
        "provenance_event_id": provenance_event_id,
    })
    evt_id = f"evt_{uuid.uuid4().hex[:8]}"
    db.execute(
        "INSERT INTO task_events (id, task_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (evt_id, task_id, "qa_remediation_initiated", event_payload, int(time.time())),
    )
    db.commit()
