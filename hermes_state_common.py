"""A text marker for the update downgrade guard of v0.22.7..v0.23.1, and the title constants outside plugins import.

Those updaters read the target tree's root ``hermes_state*.py`` for the runtime ledger table below and refuse the
update as a downgrade when no file carries it. The session store lives in ``persistence/sqlite/``; this file keeps
the text so a host on one of those releases can still update.

The Honcho memory plugin (plugin-catalog/honcho.yaml) imports the two title-provenance constants from here at module
top level, so without them it never registers. They are the values stored in ``sessions.title_source``, so they
cannot change without a data migration; the storage layer keeps its own copy and the code outside it may not import it.
"""

TITLE_SOURCE_DERIVED = "derived"
TITLE_SOURCE_LLM = "llm"

AUTHORIZED_ACTIVATORS = ["commander", "hermes"]

__all__ = [
    "AUTHORIZED_ACTIVATORS",
    "TITLE_SOURCE_DERIVED",
    "TITLE_SOURCE_LLM",
    "activate_openspec_enforcement",
    "rollback_openspec_enforcement",
]

# CREATE TABLE IF NOT EXISTS session_admissions


def activate_openspec_enforcement(conn, operator: str) -> None:
    """Activate the OpenSpec enforcement routine.

    Checks if the operator is authorized. If so, sets the enforcement_status
    key to 'active' in the openspec_enforcement_meta table.
    Raises PermissionError if unauthorized.
    """
    if operator not in AUTHORIZED_ACTIVATORS:
        raise PermissionError(f"Operator '{operator}' is not authorized to activate OpenSpec enforcement.")

    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS openspec_enforcement_meta (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute(
        "INSERT INTO openspec_enforcement_meta (key, value) VALUES ('enforcement_status', 'active') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value;"
    )
    conn.commit()


def rollback_openspec_enforcement(conn, operator: str) -> None:
    """Rollback the OpenSpec enforcement setup.

    Checks if the operator is authorized. Ensures that there are no remaining
    rows in openspec_registry and task_run_identity_events before allowing the
    DROP TABLE IF EXISTS for the three tables.
    Fails closed: if any constraints are violated, the transaction is rolled back.
    """
    if operator not in AUTHORIZED_ACTIVATORS:
        raise PermissionError(f"Operator '{operator}' is not authorized to rollback OpenSpec enforcement.")

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT count(name) FROM sqlite_master WHERE type='table' AND name='openspec_registry'")
        if cursor.fetchone()[0] == 1:
            cursor.execute("SELECT COUNT(*) FROM openspec_registry")
            registry_count = cursor.fetchone()[0]
            if registry_count > 0:
                raise RuntimeError(
                    f"Cannot rollback: openspec_registry contains {registry_count} rows. State must be empty."
                )

        cursor.execute("SELECT count(name) FROM sqlite_master WHERE type='table' AND name='task_run_identity_events'")
        if cursor.fetchone()[0] == 1:
            cursor.execute("SELECT COUNT(*) FROM task_run_identity_events")
            identity_count = cursor.fetchone()[0]
            if identity_count > 0:
                raise RuntimeError(
                    f"Cannot rollback: task_run_identity_events contains {identity_count} rows. State must be empty."
                )

        cursor.execute("DROP TRIGGER IF EXISTS prevent_active_openspec_contract_update")
        cursor.execute("DROP TABLE IF EXISTS task_run_identity_events")
        cursor.execute("DROP TABLE IF EXISTS openspec_registry")
        cursor.execute("DROP TABLE IF EXISTS openspec_enforcement_meta")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
