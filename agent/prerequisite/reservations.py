"""Bounded attempt and source-byte accounting for native preparation; not tool authorization."""
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from time import monotonic as _monotonic
from typing import Any
from weakref import WeakKeyDictionary

from agent.delegation_context import TaskIntent, current_task_intent, locked_task_intent_scope

from .catalog import MAX_SOURCE_BYTES, ResourcePin
from .descriptors import ReadDescriptor, match_operation, resolve_operation

MAX_RECEIPTS = 256


@dataclass(frozen=True, slots=True)
class _PendingRead:
    actor: str
    scope: object
    descriptor: ReadDescriptor
    invalidated: Event | None = None


@dataclass(frozen=True, slots=True)
class VerifiedDispatchPlan:
    disposition: str
    descriptor: ReadDescriptor | None = None
    cached: dict[str, bytes] = field(default_factory=dict)
    uncached: tuple[ResourcePin, ...] = ()
    scope: object | None = None


@dataclass(slots=True)
class _Preparation:
    limit: int
    deadline: float
    output_matches: Callable[[str, dict], bool] | None = None
    phase: str = "preparing"
    deadline_expired: bool = False
    seen: set[str] = field(default_factory=set)
    pending: dict[str, _PendingRead] = field(default_factory=dict)
    in_flight: dict[str, object] = field(default_factory=dict)
    source_bytes_reserved: int = 0
    cached_buffers: OrderedDict[str, bytes] = field(default_factory=OrderedDict)
    accounting_seq: int = 0
    receipts: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_RECEIPTS))

    def retire(self, phase: str) -> None:
        self.phase = phase
        self.pending.clear()
        if phase == "closed":
            self.cached_buffers.clear()

    def at_capacity(self) -> bool:
        return len(self.seen) >= self.limit

    def settle_count(self) -> None:
        # A full budget may still have valid tickets waiting to start or finish.
        if self.at_capacity() and not self.pending and not self.in_flight:
            self.retire("exhausted")

    def snapshot(self) -> dict[str, int | str | bool]:
        active = self.phase == "preparing"
        return {
            "phase": self.phase,
            "operations_limit": self.limit,
            "operations_reserved": len(self.seen),
            "operations_remaining": max(0, self.limit - len(self.seen)) if active else 0,
            "pending_tickets": len(self.pending),
            "in_flight_count": len(self.in_flight),
            "source_bytes_limit": MAX_SOURCE_BYTES,
            "source_bytes_reserved": self.source_bytes_reserved,
            "source_bytes_remaining": max(0, MAX_SOURCE_BYTES - self.source_bytes_reserved) if active else 0,
            "deadline_expired": self.deadline_expired,
        }

    def record_receipt(
        self,
        *,
        intent: TaskIntent,
        actor: str,
        call_id: str,
        tool: str,
        stage: str,
        disposition: str,
        in_flight: bool = False,
    ) -> dict[str, Any]:
        self.accounting_seq += 1
        snap = self.snapshot()
        row = {
            "schema": "hermes-prerequisite-receipt/v1",
            "accounting_seq": self.accounting_seq,
            "actor_session_id": str(actor)[:128],
            "assignment_id": intent.assignment_id,
            "tool_call_id": str(call_id)[:128],
            "tool_name": str(tool)[:64],
            "stage": stage,
            "disposition": disposition,
            "in_flight": bool(in_flight),
            "phase": snap["phase"],
            "operations_reserved": snap["operations_reserved"],
            "operations_remaining": snap["operations_remaining"],
            "operations_limit": snap["operations_limit"],
            "source_bytes_reserved": snap["source_bytes_reserved"],
            "source_bytes_remaining": snap["source_bytes_remaining"],
            "source_bytes_limit": snap["source_bytes_limit"],
        }
        self.receipts.append(row)
        return row

    def refresh(self, actor: str, scope: object) -> str:
        if self.phase != "preparing":
            return self.phase
        if _monotonic() >= self.deadline:
            self.deadline_expired = True
            self.retire("exhausted")
        else:
            stale = [
                key for key, ticket in self.pending.items()
                if ticket.scope is not scope or ticket.actor != actor
                or (ticket.invalidated is not None and ticket.invalidated.is_set())
            ]
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


def reserve_read(
    intent: TaskIntent, actor: str, call_id: str, tool: str, args: dict,
    *, invalidated: Event | None = None,
) -> str:
    """Consume one attempt for a matching descriptor; never refund it."""
    if not _valid_call(intent, call_id):
        return "unavailable"
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is None:
            return state
        if call_id in preparation.seen:
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="reserve", disposition="replay",
            )
            return "replay"
        if state != "preparing":
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="reserve", disposition=state,
            )
            return state
        if preparation.at_capacity():
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="reserve", disposition="exhausted",
            )
            return "exhausted"
    descriptor = resolve_operation(intent, actor, tool, args)
    with locked_task_intent_scope(intent, actor) as (current, _fresh, closed):
        preparation, state = _check(intent, actor, current, closed)
        if preparation is None:
            return state
        if current is not scope or current is None:
            return "inactive"
        if call_id in preparation.seen:
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="reserve", disposition="replay",
            )
            return "replay"
        if state != "preparing":
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="reserve", disposition=state,
            )
            return state
        if preparation.at_capacity():
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="reserve", disposition="exhausted",
            )
            return "exhausted"
        if descriptor is None:
            return "unavailable"
        if invalidated is not None and invalidated.is_set():
            return "inactive"
        preparation.seen.add(call_id)
        preparation.pending[call_id] = _PendingRead(actor, current, descriptor, invalidated)
        preparation.record_receipt(
            intent=intent, actor=actor, call_id=call_id, tool=tool, stage="reserve", disposition="reserved",
        )
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
        if pending.invalidated is not None and pending.invalidated.is_set():
            return "replay"
        disposition = "started" if descriptor == pending.descriptor else "mismatch"
        preparation.record_receipt(
            intent=intent, actor=actor, call_id=call_id, tool=tool, stage="begin", disposition=disposition,
        )
        return disposition


def begin_verified_read(
    intent: TaskIntent, actor: str, call_id: str, tool: str, args: dict,
) -> VerifiedDispatchPlan:
    """Serialized dispatch linearization point with atomic nonrefundable byte reservation."""
    if not _valid_call(intent, call_id):
        return VerifiedDispatchPlan("unavailable")
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        raw_prep = _preparations.get(intent)
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is None or state != "preparing":
            disp = _begin_disposition(preparation or raw_prep, state, call_id)
            if raw_prep is not None:
                raw_prep.record_receipt(
                    intent=intent, actor=actor, call_id=call_id, tool=tool, stage="begin", disposition=disp,
                )
            return VerifiedDispatchPlan(disp)
        pending = preparation.pending.get(call_id)
        if pending is None:
            disp = "replay" if call_id in preparation.seen else "unavailable"
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="begin", disposition=disp,
            )
            return VerifiedDispatchPlan(disp)
        if pending.scope is not scope or pending.actor != actor:
            return VerifiedDispatchPlan("inactive")
        matched = match_operation(intent, actor, tool, args)
        if preparation.pending.pop(call_id, None) is not pending:
            return VerifiedDispatchPlan("replay")
        if pending.invalidated is not None and pending.invalidated.is_set():
            preparation.settle_count()
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="begin", disposition="replay",
            )
            return VerifiedDispatchPlan("replay")
        if matched != pending.descriptor:
            preparation.settle_count()
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool, stage="begin", disposition="mismatch",
            )
            return VerifiedDispatchPlan("mismatch")

        cached: dict[str, bytes] = {}
        uncached: list[ResourcePin] = []
        needed_bytes = 0
        for pin in pending.descriptor.resources:
            buf = preparation.cached_buffers.get(pin.key)
            if buf is not None:
                preparation.cached_buffers.move_to_end(pin.key)
                cached[pin.key] = buf
            else:
                uncached.append(pin)
                needed_bytes += pin.max_file_bytes
        if preparation.source_bytes_reserved + needed_bytes > MAX_SOURCE_BYTES:
            preparation.settle_count()
            preparation.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool,
                stage="begin", disposition="source_byte_budget_exhausted",
            )
            return VerifiedDispatchPlan("source_byte_budget_exhausted")
        preparation.source_bytes_reserved += needed_bytes
        preparation.in_flight[call_id] = scope
        preparation.record_receipt(
            intent=intent, actor=actor, call_id=call_id, tool=tool,
            stage="begin", disposition="started", in_flight=True,
        )
        return VerifiedDispatchPlan("started", pending.descriptor, cached, tuple(uncached), scope)


def finish_verified_read(
    intent: TaskIntent,
    actor: str,
    call_id: str,
    tool: str,
    expected_scope: object | None,
    captured_uncached: dict[str, bytes] | None,
    error_code: str | None = None,
) -> str:
    """Complete an in-flight capture, reporting post-linearization revocation honestly."""
    if not _valid_call(intent, call_id):
        return "unavailable"
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        raw_prep = _preparations.get(intent)
        if raw_prep is None:
            return "closed" if closed else "unavailable"
        was_in_flight = raw_prep.in_flight.pop(call_id, None) is not None
        if closed or scope is None or scope is not expected_scope:
            if closed:
                raw_prep.retire("closed")
            disp = "revoked_in_flight" if was_in_flight else ("closed" if closed else "inactive")
            raw_prep.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool,
                stage="finish", disposition=disp, in_flight=was_in_flight,
            )
            return disp
        if error_code is not None:
            raw_prep.settle_count()
            raw_prep.record_receipt(
                intent=intent, actor=actor, call_id=call_id, tool=tool,
                stage="finish", disposition=error_code, in_flight=False,
            )
            return error_code
        if raw_prep.phase == "preparing" and captured_uncached:
            for key, data in captured_uncached.items():
                raw_prep.cached_buffers[key] = bytes(data)
                raw_prep.cached_buffers.move_to_end(key)
        raw_prep.settle_count()
        raw_prep.record_receipt(
            intent=intent, actor=actor, call_id=call_id, tool=tool,
            stage="finish", disposition="completed", in_flight=False,
        )
        return "completed"


def evict_cached_resources(intent: TaskIntent | None, actor: str, *keys: str) -> int:
    """Evict cached buffers without refunding source_bytes_reserved."""
    if type(intent) is not TaskIntent:
        return 0
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, _state = _check(intent, actor, scope, closed)
        if preparation is None:
            return 0
        targets = keys or tuple(preparation.cached_buffers.keys())
        removed = 0
        for key in targets:
            if preparation.cached_buffers.pop(key, None) is not None:
                removed += 1
        return removed


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
                preparation.record_receipt(
                    intent=intent, actor=actor, call_id=call_id, tool=pending.descriptor.tool,
                    stage="discard", disposition="discarded",
                )


def begin_output(intent: TaskIntent, actor: str) -> bool:
    """Retire unused preparation at host-confirmed output dispatch, not completion."""
    if type(intent) is not TaskIntent:
        return False
    with locked_task_intent_scope(intent, actor) as (scope, _fresh, closed):
        preparation, state = _check(intent, actor, scope, closed)
        if preparation is None or state != "preparing":
            return False
        preparation.retire("output")
        preparation.record_receipt(
            intent=intent, actor=actor, call_id="output-transition", tool="output",
            stage="transition", disposition="output",
        )
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
                preparation.record_receipt(
                    intent=intent, actor=actor, call_id="output-dispatch", tool=tool,
                    stage="transition", disposition=retire_as,
                )


def accounting_snapshot(
    actor_session_id: str, parent_session_id: str, assignment_id: str = "",
) -> dict[str, int | str | bool] | None:
    """Return bounded nonsecret prerequisite counters for the active assignment."""
    intent = current_task_intent(actor_session_id, parent_session_id)
    if intent is None or (assignment_id and intent.assignment_id != assignment_id):
        return None
    with locked_task_intent_scope(intent, actor_session_id) as (scope, _fresh, closed):
        raw_prep = _preparations.get(intent)
        preparation, _state = _check(intent, actor_session_id, scope, closed)
        target = preparation or raw_prep
        return None if target is None else target.snapshot()


def preparation_snapshot_for_intent(intent: TaskIntent | None) -> dict[str, int | str | bool] | None:
    """Inspect nonsecret accounting state for a known TaskIntent handle."""
    if type(intent) is not TaskIntent:
        return None
    with locked_task_intent_scope(intent, intent.actor_session_id) as (_scope, _fresh, closed):
        prep = _preparations.get(intent)
        if prep is None:
            return None
        if closed and prep.phase == "preparing":
            prep.retire("closed")
        return prep.snapshot()


def assignment_receipts(intent: TaskIntent | None) -> list[dict[str, Any]]:
    """Return bounded nonsecret host receipts recorded for *intent*."""
    if type(intent) is not TaskIntent:
        return []
    with locked_task_intent_scope(intent, intent.actor_session_id) as (_scope, _fresh, _closed):
        prep = _preparations.get(intent)
        return [] if prep is None else [dict(row) for row in prep.receipts]
