"""Context-local state for delegate_task child execution.

A Hermes process may itself be a Kanban dispatcher worker with HERMES_KANBAN_* in
os.environ. In-process delegate_task children and cron jobs fired via
``cronjob(action="run")`` are NOT dispatcher-owned, so identity gates must fail
closed for them without mutating the process-global environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from weakref import WeakKeyDictionary, WeakValueDictionary
from uuid import uuid4
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Mapping, MutableMapping, overload

from agent.prerequisite_catalog import ProposalSelection
from agent.prerequisite_workflow import child_proposal_selection

_DELEGATED_CHILD_CONTEXT: ContextVar[bool] = ContextVar("hermes_delegated_child_context", default=False)
# Any in-process execution that is NOT the dispatcher-owned worker (cron jobs). Kept separate
# so delegate_task-specific behaviour (subprocess env scrubbing, its error strings) is unchanged.
_NON_DISPATCHER_OWNED_CONTEXT: ContextVar[bool] = ContextVar("hermes_non_dispatcher_owned_context", default=False)

@dataclass(frozen=True, eq=False)
class TaskIntent:
    """Host provenance for a native assignment; carries no tool authorization."""

    version: int
    assignment_id: str
    actor_session_id: str
    parent_session_id: str
    goal_text: str
    goal_hash: str
    context_hash: str
    proposal_selection: ProposalSelection | None = None


_TASK_INTENT: ContextVar[TaskIntent | None] = ContextVar("hermes_task_intent", default=None)


# The scope token is per activation, including resumes. A copied ContextVar
# cannot become live again when the same assignment enters a later turn.
_ACTIVE_LOCK = RLock()
_ACTIVE: dict[str, tuple[TaskIntent, object]] = {}
_LATEST: WeakValueDictionary[str, TaskIntent] = WeakValueDictionary()
# The immutable record identifies the original actor. Only a host-confirmed
# compression transition moves its current physical segment.
_SEGMENT: WeakKeyDictionary[TaskIntent, str] = WeakKeyDictionary()
_SCOPE: ContextVar[object | None] = ContextVar("hermes_assignment_scope", default=None)


class _ResumeIntent:
    """One-use in-process handoff. Never serialize or rebuild from history."""

    def __init__(self, record: TaskIntent):
        self.record: TaskIntent | None = record

    def take(self, actor: str, parent: str) -> TaskIntent | None:
        with _ACTIVE_LOCK:
            record, self.record = self.record, None
            if record and _SEGMENT.get(record) == actor and record.parent_session_id == parent:
                return record
            return None


def revoke_task_intent(record: TaskIntent | None) -> None:
    """Invalidate a running worker without revoking a replacement actor's assignment."""
    if type(record) is TaskIntent:
        with _ACTIVE_LOCK:
            segment = _SEGMENT.pop(record, None)
            if segment is not None and _LATEST.get(segment) is record:
                del _LATEST[segment]
                _ACTIVE.pop(segment, None)


def stop_child_task_intent(child) -> None:
    """Retire this worker's provenance even if it has not started yet."""
    if child is None:
        return
    with _ACTIVE_LOCK:
        child._delegate_intent_stopped = True
        revoke_task_intent(getattr(child, "_delegate_running_intent", None))


@contextmanager
def native_task_intent(
    *, actor_session_id: str, parent_session_id: str, goal: str, context: str | None,
    resumed: bool = False, handoff: _ResumeIntent | None = None, owner=None, child_task_id: str = "",
) -> Iterator[TaskIntent | None]:
    """Bind separate native producer inputs, never deserialize a model envelope.

    Unknown resumed state receives no fresh provenance. Lifecycle retention is
    owned by the native assignment owner, not reconstructed from transcript text.
    """
    valid = all(type(value) is str and value.strip() for value in (actor_session_id, parent_session_id, goal))
    valid = valid and (context is None or type(context) is str)
    record = None
    if valid and not resumed:
        try:
            goal_hash = sha256(goal.encode("utf-8")).hexdigest()
            context_hash = sha256((context or "").encode("utf-8")).hexdigest()
        except UnicodeEncodeError:
            pass  # Invalid text retains legacy handling, never partial provenance.
        else:
            record = TaskIntent(
                1, uuid4().hex, actor_session_id, parent_session_id, goal, goal_hash, context_hash,
                child_proposal_selection(parent_session_id, child_task_id=child_task_id),
            )
    scope = object()
    with _ACTIVE_LOCK:
        if valid and resumed and type(handoff) is _ResumeIntent:
            record = handoff.take(actor_session_id, parent_session_id)
            if _LATEST.get(actor_session_id) is not record or actor_session_id in _ACTIVE:
                record = None
        if owner is not None:
            if getattr(owner, "_delegate_intent_stopped", False) is True:
                revoke_task_intent(record)
                record = None
            owner._delegate_running_intent = record
        if record is not None:
            _SEGMENT[record] = actor_session_id
            _LATEST[actor_session_id] = record
            _ACTIVE[actor_session_id] = (record, scope)
    token = _TASK_INTENT.set(record)
    scope_token = _SCOPE.set(scope)
    try:
        yield record
    finally:
        if record is not None:
            with _ACTIVE_LOCK:
                segment = _SEGMENT.get(record)
                if segment is not None and _ACTIVE.get(segment) == (record, scope):
                    del _ACTIVE[segment]
        _SCOPE.reset(scope_token)
        _TASK_INTENT.reset(token)


def current_task_intent(actor_session_id: str, parent_session_id: str) -> TaskIntent | None:
    """Return only a versioned, identity-bound record from the active host scope."""
    record = _TASK_INTENT.get()
    if type(record) is not TaskIntent or type(record.version) is not int or record.version != 1:
        return None
    text_fields = (record.actor_session_id, record.parent_session_id, record.goal_text,
                   actor_session_id, parent_session_id)
    if any(type(value) is not str or not value.strip() for value in text_fields):
        return None
    for value, length in ((record.assignment_id, 32), (record.goal_hash, 64), (record.context_hash, 64)):
        if type(value) is not str or len(value) != length or any(c not in "0123456789abcdef" for c in value):
            return None
    if record.parent_session_id != parent_session_id:
        return None
    try:
        goal_hash = sha256(record.goal_text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return None
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(actor_session_id)
        return record if record.goal_hash == goal_hash and active == (record, _SCOPE.get()) else None


def rebind_compressed_task_intent(old: str, new: str, parent: str | None) -> None:
    """Move only the calling native scope, after host publication/adoption succeeds."""
    if parent is None or type(new) is not str or not new.strip() or old == new:
        return
    with _ACTIVE_LOCK:
        record = current_task_intent(old, parent)
        if record is None:
            return  # DB-only recovery or a late callback cannot create a binding.
        if new in _LATEST:
            revoke_task_intent(record)
            return  # Never displace a different assignment at the destination.
        _ACTIVE[new] = _ACTIVE.pop(old)
        del _LATEST[old]
        _LATEST[new] = record
        _SEGMENT[record] = new


DELEGATED_CHILD_ENV_MARKER = "HERMES_DELEGATED_CHILD_CONTEXT"

KANBAN_ENV_KEYS: tuple[str, ...] = (
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
    "HERMES_KANBAN_GOAL_MODE", "HERMES_KANBAN_GOAL_MAX_TURNS",
)


@contextmanager
def delegated_child_context(session_id: str | None = None) -> Iterator[None]:
    """Mark child execution and isolate its task-local session identity. Even a context
    entered without an id must restore the parent's session ContextVar (child
    construction calls ``set_current_session_id``)."""
    token = _DELEGATED_CHILD_CONTEXT.set(True)
    try:
        from gateway.session_context import scoped_current_session_id  # lazy: it calls is_delegated_child_context()

        with scoped_current_session_id(session_id):
            yield
    finally:
        _DELEGATED_CHILD_CONTEXT.reset(token)


def is_delegated_child_context() -> bool:
    """Return True while code is running for a delegate_task child."""
    return bool(_DELEGATED_CHILD_CONTEXT.get())


def enter_non_dispatcher_owned_context() -> Token[bool]:
    """Token form of :func:`non_dispatcher_owned_context` for long try/finally scopes."""
    return _NON_DISPATCHER_OWNED_CONTEXT.set(True)


def exit_non_dispatcher_owned_context(token: Token[bool]) -> None:
    """Restore the flag saved by :func:`enter_non_dispatcher_owned_context`."""
    _NON_DISPATCHER_OWNED_CONTEXT.reset(token)


@contextmanager
def non_dispatcher_owned_context() -> Iterator[None]:
    """Mark in-process execution that does NOT own the dispatcher's Kanban task; without it
    a cron agent run inside a worker is misread as that worker (kanban toolset force-added,
    ``kanban_complete`` defaulting to its task). ContextVar-scoped rather than clearing
    os.environ, which the worker's claim heartbeat and concurrent readers share."""
    token = enter_non_dispatcher_owned_context()
    try:
        yield
    finally:
        exit_non_dispatcher_owned_context(token)


def is_dispatcher_owned_worker_context() -> bool:
    """The single predicate every ``HERMES_KANBAN_*`` identity gate should use."""
    return not (is_delegated_child_process_context() or _NON_DISPATCHER_OWNED_CONTEXT.get())


def explicit_board_intent_is_pinned() -> bool:
    """Whether an explicit kanban ``board=`` argument must still resolve through
    the dispatcher-injected env pins (``HERMES_KANBAN_DB`` & friends) rather than
    its own board directory.

    True for in-process delegate children, descendants carrying
    :data:`DELEGATED_CHILD_ENV_MARKER`, and dispatched workers
    (``HERMES_KANBAN_TASK`` set). The pins are the "workers physically cannot
    see other boards" isolation, and :func:`kanban_path_is_fenced` checks the
    pinned path / fenced root — an explicit board that resolved elsewhere would
    also escape that fence. Outside these fences an explicit board is the
    caller's own intent and wins.
    """
    if _DELEGATED_CHILD_CONTEXT.get():
        return True
    if os.environ.get(DELEGATED_CHILD_ENV_MARKER):
        return True
    return bool((os.environ.get("HERMES_KANBAN_TASK") or "").strip())


def owned_kanban_task() -> str:
    """The board task this execution OWNS: ``HERMES_KANBAN_TASK`` for the dispatcher-owned
    worker, ``""`` otherwise. Tool access is not worker identity — a profile can expose the
    kanban toolset interactively, and children/cron runs inherit the env var — so every
    reader that turns the task id into worker behaviour (guidance, stop nudge, terminal
    outcomes) goes through this one helper."""
    if not is_dispatcher_owned_worker_context():
        return ""
    return (os.environ.get("HERMES_KANBAN_TASK") or "").strip()


def is_delegated_child_process_context() -> bool:
    """Return True in this process or a subprocess spawned by a child."""
    return bool(_DELEGATED_CHILD_CONTEXT.get()) or bool(os.environ.get(DELEGATED_CHILD_ENV_MARKER))


def _fenced_kanban_root() -> str:
    """The board root this process's Kanban lineage lives under (``kanban_home()``); ``"1"`` when it
    cannot be resolved, which readers treat as "fence every board" (the pre-path marker)."""
    try:
        from hermes_cli.kanban_db import kanban_home
        return str(kanban_home())
    except Exception:
        return "1"


def scrub_kanban_env(env: Mapping[str, str] | MutableMapping[str, str]) -> dict[str, str]:
    """Remove worker identity, retaining board/location and an inherited write fence.

    TASK absence alone would promote a descendant to an orchestrator. The marker
    survives later execs, including scripts that remove TASK themselves. This is
    cooperative runtime scoping, not confinement of code with direct SQLite access.

    The marker's value is the fenced board ROOT, so the fence applies to the lineage's
    board and not to every Kanban DB the descendant touches: a child running a repro
    against a temp ``HERMES_HOME`` got a silently read-only board there. An inherited
    path-valued marker is kept (a grandchild that moved HERMES_HOME must not re-fence
    onto its scratch root and unfence the real one).
    """
    cleaned = {k: v for k, v in env.items() if k not in KANBAN_ENV_KEYS}
    # Only the gateway mints ``kanban`` sessions: a descendant ``hermes chat`` sending it is refused.
    if cleaned.get("HERMES_SESSION_SOURCE") == "kanban":
        del cleaned["HERMES_SESSION_SOURCE"]
    inherited = str(env.get(DELEGATED_CHILD_ENV_MARKER) or "")
    cleaned[DELEGATED_CHILD_ENV_MARKER] = inherited if inherited and inherited != "1" else _fenced_kanban_root()
    return cleaned


def kanban_path_is_fenced(path: os.PathLike[str] | str) -> bool:
    """Whether Kanban mutations at *path* (a board DB or board-metadata root) are denied for this
    process: always for an in-process delegate child (the parent's own board); for a spawned
    descendant only when *path* is the dispatcher-pinned ``HERMES_KANBAN_DB`` or lies under the
    fenced root the marker carries. A legacy ``"1"`` marker fences everything."""
    if _DELEGATED_CHILD_CONTEXT.get():
        return True
    marker = os.environ.get(DELEGATED_CHILD_ENV_MARKER, "")
    if not marker:
        return False
    if marker == "1":
        return True
    from pathlib import Path
    target = Path(path).expanduser().resolve()
    pinned = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if pinned and target == Path(pinned).expanduser().resolve():
        return True
    try:
        target.relative_to(Path(marker).expanduser().resolve())
    except ValueError:
        return False
    return True


@overload
def delegated_child_subprocess_env(env: Mapping[str, str]) -> dict[str, str]: ...


@overload
def delegated_child_subprocess_env(env: None = None) -> dict[str, str] | None: ...


def delegated_child_subprocess_env(
    env: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Carry worker/delegate descendant denial across a real process spawn.

    Location and credentials are untouched; callers retain their existing secret policy.
    Dispatcher workers and supervised tool transports grant their own explicit scope.
    """
    if not (is_delegated_child_process_context() or os.environ.get("HERMES_KANBAN_TASK")
            or (env and (env.get("HERMES_KANBAN_TASK") or env.get(DELEGATED_CHILD_ENV_MARKER)))):
        return None if env is None else dict(env)
    return scrub_kanban_env(os.environ if env is None else env)
