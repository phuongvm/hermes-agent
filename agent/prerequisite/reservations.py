"""Bounded attempt accounting for native preparation; not tool authorization."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic as _monotonic
from weakref import WeakKeyDictionary

from agent.delegation_context import TaskIntent, current_task_intent, locked_task_intent_scope

from .descriptors import ReadDescriptor, resolve_operation


@dataclass(frozen=True, slots=True)
class _PendingRead:
    actor: str
    scope: object
    descriptor: ReadDescriptor


@dataclass(slots=True)
class _Preparation:
    limit: int
    deadline: float
    output_matches: Callable[[str, dict], bool] | None = None
    phase: str = "preparing"
    deadline_expired: bool = False
    seen: set[str] = field(default_factory=set)
    pending: dict[str, _PendingRead] = field(default_factory=dict)

    def retire(self, phase: str) -> None:
        self.phase = phase
        self.pending.clear()

    def at_capacity(self) -> bool:
        return len(self.seen) >= self.limit

    def settle_count(self) -> None:
        # A full budget may still have valid tickets waiting to start.
        if self.at_capacity() and not self.pending:
            self.retire("exhausted")

    def refresh(self, actor: str, scope: object) -> str:
        if self.phase != "preparing":
            return self.phase
        if _monotonic() >= self.deadline:
            self.deadline_expired = True
            self.retire("exhausted")
        else:
            stale = [key for key, ticket in self.pending.items() if ticket.scope is not scope or ticket.actor != actor]
            for key in stale:
                del self.pending[key]
            self.settle_count()
        return self.phase


# All accesses are under the host lifecycle lock. Values do not keep keys alive.
_preparations: WeakKeyDictionary[TaskIntent, _Preparation] = WeakKeyDictionary()


def start_preparation(
    intent: TaskIntent | None, actor: str, operations: int, seconds: int,
    *, output_matches: Callable[[str, dict], bool] | None = None,
) -> bool:
    """Initialize once in the fresh native activation, never on continuation."""
    if (type(intent) is not TaskIntent or intent.proposal_selection is None
            or type(operations) is not int or not 1 <= operations <= 128
            or type(seconds) is not int or not 1 <= seconds <= 900):
        return False
    selection = intent.proposal_selection
    with locked_task_intent_scope(intent, actor) as (scope, fresh, _closed):
        if scope is None or not fresh or intent in _preparations:
            return False
        _preparations[intent] = _Preparation(
            min(operations, selection.operations), _monotonic() + min(seconds, selection.seconds),
            output_matches,
        )
        return True


def _check(intent: TaskIntent, actor: str, scope: object | None, closed: bool) -> tuple[_Preparation | None, str]:
    preparation = _preparations.get(intent)
    if closed:
        if preparation is not None:
            preparation.retire("closed")
        return None, "closed"
    if scope is None:
        return None, "inactive"
    if preparation is None:
        return None, "unavailable"
    return preparation, preparation.refresh(actor, scope)


def _valid_call(intent: TaskIntent | None, call_id: str) -> bool:
    return type(intent) is TaskIntent and type(call_id) is str and 0 < len(call_id) <= 256


def reserve_read(intent: TaskIntent, actor: str, call_id: str, tool: str, args: dict) -> str:
    """Consume one attempt for a matching descriptor; never refund it."""
    if not _valid_call(intent, call_id):
        return "unavailable"
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is None:
            return state
        if call_id in preparation.seen:
            return "replay"
        if state != "preparing":
            return state
        if preparation.at_capacity():
            return "exhausted"
    descriptor = resolve_operation(intent, actor, tool, args)
    with locked_task_intent_scope(intent, actor) as (current, _fresh, closed):
        preparation, state = _check(intent, actor, current, closed)
        if preparation is None:
            return state
        if current is not scope or current is None:
            return "inactive"
        if call_id in preparation.seen:
            return "replay"
        if state != "preparing":
            return state
        if preparation.at_capacity():
            return "exhausted"
        if descriptor is None:
            return "unavailable"
        preparation.seen.add(call_id)
        preparation.pending[call_id] = _PendingRead(actor, current, descriptor)
        return "reserved"


def begin_read(intent: TaskIntent, actor: str, call_id: str, tool: str, args: dict) -> str:
    """Spend the pending ticket at the dispatch point, after independent checks.

    A started ticket is bookkeeping, not permission or a verified file handle.
    """
    if not _valid_call(intent, call_id):
        return "unavailable"
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is None or state != "preparing":
            return _begin_disposition(preparation, state, call_id)
        pending = preparation.pending.get(call_id)
        if pending is None:
            return "replay" if call_id in preparation.seen else "unavailable"
        if pending.scope is not scope or pending.actor != actor:
            return "inactive"
    descriptor = resolve_operation(intent, actor, tool, args)
    with locked_task_intent_scope(intent, actor) as (current, _fresh, closed):
        preparation, state = _check(intent, actor, current, closed)
        if preparation is None or state != "preparing":
            return _begin_disposition(preparation, state, call_id)
        if current is not scope:
            return "inactive"
        if preparation.pending.pop(call_id, None) is not pending:
            return "replay"
        preparation.settle_count()
        return "started" if descriptor == pending.descriptor else "mismatch"


def _begin_disposition(preparation: _Preparation | None, state: str, call_id: str) -> str:
    if (preparation is not None and state == "exhausted" and not preparation.deadline_expired
            and call_id in preparation.seen):
        return "replay"
    return state


def discard_read(intent: TaskIntent, actor: str, call_id: str) -> None:
    """Retire an unstarted ticket after cancellation or another guard's veto."""
    if not _valid_call(intent, call_id):
        return
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is not None and state == "preparing":
            pending = preparation.pending.get(call_id)
            if pending is not None and pending.scope is scope and pending.actor == actor:
                del preparation.pending[call_id]
                preparation.settle_count()


def begin_output(intent: TaskIntent, actor: str) -> bool:
    """Retire unused preparation at host-confirmed output dispatch, not completion."""
    if type(intent) is not TaskIntent:
        return False
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is None or state != "preparing":
            return False
        preparation.retire("output")
        return True


def note_output_dispatch(actor: str, parent: str, tool: str, args: dict) -> None:
    """Observe final arguments of an already-authorized dispatch; never grant permission."""
    intent = current_task_intent(actor, parent)
    if intent is None:
        return
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is None or state != "preparing" or preparation.output_matches is None:
            return
        matches = preparation.output_matches
    try:
        retire_as = "output" if matches(tool, args) else None
    except Exception:  # health: allow BLE001 -- Retire on any classifier failure without logging tool arguments.
        # A failed classification cannot preserve the exception to ordinary controls.
        retire_as = "closed"
    if retire_as is not None:
        with locked_task_intent_scope(intent, actor) as (current, _fresh, closed):
            preparation, state = _check(intent, actor, current, closed)
            if current is scope and preparation is not None and state == "preparing":
                preparation.retire(retire_as)
