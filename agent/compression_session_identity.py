"""In-memory identity and context updates after compression publishes a segment."""
from __future__ import annotations

import os
from contextlib import suppress
from typing import Any

from agent.delegation_context import rebind_compressed_task_intent


def activate_compressed_segment(agent: Any, old_id: str, new_id: str) -> None:
    """Keep existing assignment provenance when the host accepts a new segment."""
    rebind_compressed_task_intent(old_id, new_id, getattr(agent, "_parent_session_id", None))
    agent.session_id = new_id


def rebind_session_context(session_id: str) -> None:
    """Point the worker thread's session ContextVar and log context at the new ID."""
    try:
        from gateway.session_context import set_current_session_id
        set_current_session_id(session_id)
    except (ImportError, AttributeError):
        os.environ["HERMES_SESSION_ID"] = session_id
    with suppress(Exception):
        from hermes_logging import set_session_context
        set_session_context(session_id)
