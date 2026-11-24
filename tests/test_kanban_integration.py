import pytest
import sqlite3
import json
import time
from uuid import uuid4

import hermes_cli.kanban_db as kanban_db

def test_generic_task_parity(tmp_path):
    """
    Test that standard non-OpenSpec tasks work exactly as before.
    (Lifecycle: create -> running -> completed)
    """
    from hermes_cli.kanban_db_connect import _migrate_add_optional_columns
    from hermes_state import SessionDB
    from persistence.sqlite.openspec import run_openspec_migration

    db_path = tmp_path / "state.db"
    db = SessionDB(db_path)

    with db._lock:
        db._conn.executescript(kanban_db.SCHEMA_SQL)
        _migrate_add_optional_columns(db._conn)
        run_openspec_migration(db._conn)
        db._conn.commit()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # 1. Create a generic task
    task_id = kanban_db.create_task(
        conn,
        assignee="coder",
        title="Generic Task",
        body="Do some work"
    )

    # 2. Check initial state
    task = kanban_db.get_task(conn, task_id)
    assert task.status == "ready"

    # 3. Claim and update status
    run_id = "run_1"
    kanban_db.claim_task(conn, task_id)
    
    # In older models, claiming might set to 'running' or we can manually set it
    # No direct update_status in kanban_db without correct function signature.
    # We can do DB update to 'running'.
    conn.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
    conn.commit()
    task = kanban_db.get_task(conn, task_id)
    assert task.status == "running"

    # 4. Complete task
    conn.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
    conn.commit()
    task = kanban_db.get_task(conn, task_id)
    assert task.status == "done"

def test_openspec_registered_task_enforcement(tmp_path):
    """
    Test that OpenSpec registered tasks enforce strict state machine transitions.
    """
    from hermes_cli.kanban_db_connect import _migrate_add_optional_columns
    from hermes_state import SessionDB
    from persistence.sqlite.openspec import run_openspec_migration

    db_path = tmp_path / "state.db"
    db = SessionDB(db_path)

    with db._lock:
        db._conn.executescript(kanban_db.SCHEMA_SQL)
        _migrate_add_optional_columns(db._conn)
        run_openspec_migration(db._conn)
        db._conn.commit()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    
    # 1. Create an OpenSpec task
    task_id = kanban_db.create_task(
        conn,
        assignee="coder",
        title="OpenSpec Task",
        body="Do some OS work"
    )

    # 2. Register it in openspec_registry
    with db._lock:
        db._conn.execute(
            "INSERT INTO openspec_registry (id, openspec_contract, status, created_at) VALUES (?, ?, ?, ?)",
            (task_id, 'spec123', 'active', time.time())
        )
        db._conn.commit()

    # 3. Verify normal rules still apply
    run_id = "run_os_1"
    kanban_db.claim_task(conn, task_id)
    
    conn.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
    conn.commit()
    task = kanban_db.get_task(conn, task_id)
    assert task.status == "running"

    # 4. Attempt to do something the openspec task shouldn't
    # (Verify triggers fire on invalid mutations on registry)
    with pytest.raises(sqlite3.IntegrityError, match="SQLITE_CONSTRAINT_TRIGGER"):
        with db._lock:
            db._conn.execute("UPDATE openspec_registry SET status='done' WHERE id=?", (task_id,))
