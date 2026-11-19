"""Host-owned handoff between progress supervision and the guarded executor."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterator

from agent.delegation_context import TaskIntent, current_task_intent
from agent.inline_tool_executors import tool_hook_ids
from . import reservations


@dataclass(slots=True)
class GuardedRead:
    intent: TaskIntent
    actor: str
    call_id: str
    tool: str
    _lock: Lock = field(default_factory=Lock)
    _closed: bool = False
    _restricted: bool = False
    _owns_ticket: bool = False

    @property
    def requires_verified_reader(self) -> bool:
        with self._lock:
            return self._restricted

    def reserve(self, args: dict) -> str:
        with self._lock:
            if self._closed:
                return "inactive"
        try:
            decision = reservations.reserve_read(self.intent, self.actor, self.call_id, self.tool, args)
        except Exception:  # health: allow BLE001 -- Fence a bookkeeping failure without exposing arguments.
            with self._lock:
                self._restricted = True
            raise
        with self._lock:
            if not self._closed:
                self._restricted |= decision in {"reserved", "replay"}
                self._owns_ticket |= decision == "reserved"
                return decision
        if decision == "reserved":
            self._discard_owned_ticket()
        return "inactive"

    def close(self) -> None:
        with self._lock:
            self._closed = True
            owns_ticket = self._owns_ticket
        if owns_ticket:
            self._discard_owned_ticket()

    def _discard_owned_ticket(self) -> None:
        try:
            reservations.discard_read(self.intent, self.actor, self.call_id)
        except Exception:  # health: allow BLE001 -- Keep the reader fenced and preserve any independent veto.
            with self._lock:
                self._restricted = True


_current: ContextVar[GuardedRead | None] = ContextVar("prerequisite_guarded_read", default=None)


@contextmanager
def guarded_read_scope(actor: str, parent: str, call_id: str, tool: str) -> Iterator[GuardedRead | None]:
    intent = current_task_intent(actor, parent)
    call = GuardedRead(intent, actor, call_id, tool) if intent is not None and intent.proposal_selection is not None else None
    token = _current.set(call)
    try:
        yield call
    finally:
        _current.reset(token)
        if call is not None:
            call.close()


def reserve_progress_read(actor: str, assignment_id: str, call_id: str, tool: str, args: dict) -> str:
    """Reserve for the current host call without changing any other guard."""
    call = _current.get()
    if call is None or (actor, assignment_id, call_id, tool) != (
        call.actor, call.intent.assignment_id, call.call_id, call.tool,
    ):
        return "unavailable"
    return call.reserve(args)


def pre_tool_block(agent, ref):
    """Return the plugin veto (if any) and final rewritten arguments."""
    try:
        from hermes_cli.plugins import _dispatch_pre_tool_call_hooks

        block_msg, modified_args = _dispatch_pre_tool_call_hooks(
            ref.name, ref.args, **tool_hook_ids(agent, ref.task_id, ref.call_id),
            middleware_trace=list(ref.trace),
        )
        return block_msg, (ref.args if modified_args is None else modified_args)
    except Exception:  # health: allow BLE001 -- Preserve executor fallback for a failed hook dispatcher.
        return None, ref.args
