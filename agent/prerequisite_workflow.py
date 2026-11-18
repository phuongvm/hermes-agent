"""In-process provenance for an explicit, admitted proposal command. No tool grants."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from types import MethodType
from weakref import ref

from agent.prerequisite_catalog import (
    WORKFLOW, InvalidCatalog, ProposalSelection, freeze_proposal_catalog, proposal_entries, valid_change_id,
    verify_catalog_metadata,
)

logger = logging.getLogger(__name__)


def _unavailable(reason: str) -> None:
    logger.info("PREREQUISITE_SELECTION_UNAVAILABLE: %s", reason)


def _selection(change: str) -> ProposalSelection | None:
    from hermes_cli.config_backend import ConfigBackendUnavailable
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_yaml import YAMLError

    try:
        config = load_user_config_effective(fail_closed=True, strict_managed=True)
        catalog = config.get("prerequisite_catalog", {})
        if catalog == {}:
            raise InvalidCatalog("catalog-absent")
        entries = proposal_entries(catalog)
        if change not in entries:
            raise InvalidCatalog("change-not-configured")
        return freeze_proposal_catalog(change, entries[change])
    except InvalidCatalog as exc:
        _unavailable(str(exc))
    except (ConfigBackendUnavailable, OSError, TypeError, ValueError, YAMLError):
        _unavailable("profile-config-unavailable")
    return None  # Invalid or absent catalog never changes ordinary controls.


@dataclass(frozen=True)
class _Command:
    event: ref
    route: str
    selection: ProposalSelection


@dataclass(frozen=True, slots=True)
class _AuthenticatedCommand:
    event: ref
    text: str
    sender: str


@dataclass(slots=True)
class _OriginalDelivery:
    event: ref
    text: str
    sender: str | None
    active: bool = True


_delivery: ContextVar[_OriginalDelivery | None] = ContextVar("hermes_original_proposal_delivery", default=None)


@contextmanager
def _original_delivery_scope(event):
    received = (_OriginalDelivery(ref(event), event.text, event.sender_id())
                if not event.internal and event.get_command() == WORKFLOW else None)
    token = _delivery.set(received)
    try:
        yield
    finally:
        if received is not None:
            received.active = False
        _delivery.reset(token)


def original_proposal_delivery(handler):
    """Wrap only adapter callbacks; routing provenance may be carried, this receipt may not."""
    async def dispatch(event, *args, **kwargs):
        with _original_delivery_scope(event):
            return await handler(event, *args, **kwargs)

    owner = getattr(handler, "__self__", None)
    if owner is None:
        return dispatch

    # The adapter uses the bound runner to route busy messages to durable FIFO.
    async def bound_dispatch(_owner, event, *args, **kwargs):
        return await dispatch(event, *args, **kwargs)

    return MethodType(bound_dispatch, owner)


def authenticate_proposal_command(event, original_text: str) -> None:
    """Capture only the unchanged original command after ingress authorization."""
    vars(event).pop("_authenticated_proposal_command", None)
    sender = event.sender_id()
    delivery = _delivery.get()
    if (delivery is None or not delivery.active or delivery.event() is not event
            or delivery.sender != sender or delivery.text != original_text):
        return
    if (event.internal or not event.allow_gateway_control or getattr(event.source, "is_bot", False)
            or not sender or event.text != original_text or event.get_command() != WORKFLOW):
        return
    event._authenticated_proposal_command = _AuthenticatedCommand(ref(event), original_text, sender)


def proposal_skill_receipt(event, route, argument, *, stacked: bool) -> Callable[[str, str | None], None] | None:
    """Return a host-only observer of the renderer's single successful load."""
    delivery = _delivery.get()
    if (delivery is None or not delivery.active or delivery.event() is not event
            or not isinstance(getattr(event, "_authenticated_proposal_command", None), _AuthenticatedCommand)):
        return None
    if stacked:
        vars(event).pop("_authenticated_proposal_command", None)
        _unavailable("invalid-change-selector")
        return None

    def resolved(name: str, path: str | None) -> None:
        note_proposal_command(event, route, name, argument, path)

    return resolved


def note_proposal_command(event, route, skill, argument, skill_path):
    """Called after successful skill resolution on the authorized inbound command path."""
    receipt = vars(event).pop("_authenticated_proposal_command", None)
    delivery = _delivery.get()
    if (delivery is None or not delivery.active or delivery.event() is not event):
        return
    if (skill != WORKFLOW or event.internal or not event.allow_gateway_control or not route
            or type(receipt) is not _AuthenticatedCommand or receipt.event() is not event
            or receipt.text != event.text or receipt.sender != event.sender_id()):
        return
    if not valid_change_id(argument) or argument != event.get_command_args().strip():
        _unavailable("invalid-change-selector")
        return
    selection = _selection(argument)
    if selection is not None:
        pin = next(pin for pin in selection.resources if pin.key == f"skill/{WORKFLOW}")
        if not isinstance(skill_path, str) or Path(skill_path) != Path(pin.path):
            _unavailable("resolved-skill-mismatch")
            return
        event._proposal_command = _Command(ref(event), route, selection)


_QUEUED_LIMIT = 128
_QUEUED_SECONDS = 120


@dataclass(frozen=True, slots=True)
class _ProposalTicket:
    expires: float
    session_id: str
    route: str
    text_digest: bytes
    selection: ProposalSelection


def queue_proposal_command(authority, event, row) -> None:
    """Transfer an admitted command to the process-local FIFO sidecar."""
    command = vars(event).pop("_proposal_command", None)
    payload = row.get("payload", {})
    native = payload.get("native_text_v1", {})
    text = payload.get("text")
    if type(command) is not _Command:
        return
    if not row.get("_created_this_write"):
        return  # exact redelivery must not replace the ticket for the first delivery
    if (row.get("status") != "queued" or command.event() is not event
            or command.route != native.get("route") or not isinstance(text, str) or text != event.text):
        _unavailable("admission-binding-mismatch")
        return
    now = time.monotonic()
    pending = authority.proposal_commands
    for key, item in tuple(pending.items()):
        if item.expires <= now:
            del pending[key]
            _unavailable("admission-ticket-expired")
    if len(pending) < _QUEUED_LIMIT:
        pending[row["admission_id"]] = _ProposalTicket(
            now + _QUEUED_SECONDS, row["target_session_id"], command.route,
            sha256(text.encode("utf-8", "surrogatepass")).digest(), command.selection,
        )
    else:
        _unavailable("admission-ticket-capacity")


def restore_proposal_command(ticket: _ProposalTicket, event, route, session_id, row) -> None:
    if time.monotonic() >= ticket.expires:
        _unavailable("admission-ticket-expired")
        return
    if (event.internal or not event.allow_gateway_control or row.get("status") != "started"
            or (ticket.session_id, ticket.route) != (session_id, route) or not isinstance(event.text, str)
            or ticket.text_digest != sha256(event.text.encode("utf-8", "surrogatepass")).digest()):
        _unavailable("admission-binding-mismatch")
        return
    event._proposal_command = _Command(ref(event), route, ticket.selection)


@dataclass(frozen=True, slots=True)
class _ActiveProposal:
    key: object
    parent: str
    selection: ProposalSelection


_active: ContextVar[_ActiveProposal | None] = ContextVar(
    "hermes_proposal_command", default=None
)
_lock = RLock()
_live: set[object] = set()


@contextmanager
def proposal_turn(event, route, parent):
    command = vars(event).pop("_proposal_command", None)
    key = object()
    binding = None
    if (type(command) is _Command and command.event() is event and command.route == route and parent
            and not event.internal and event.allow_gateway_control):
        binding = _ActiveProposal(key, parent, command.selection)
        with _lock:
            _live.add(key)
    token = _active.set(binding)
    try:
        yield
    finally:
        with _lock:
            _live.discard(key)
        _active.reset(token)


def retire_proposal_turn() -> None:
    """Invalidate the current command, including contexts copied by its workers."""
    binding = _active.get()
    if binding is not None:
        with _lock:
            _live.discard(binding.key)
        _active.set(None)


def _child_targets_planning_home(task_id: str, planning_home: str) -> bool:
    from tools.terminal_scope import TerminalPolicyUnavailable, terminal_env
    from tools.terminal_tool import get_session_cwd

    if not task_id:
        return False
    try:
        if terminal_env("TERMINAL_ENV", "local").strip().lower() != "local":
            return False
        cwd = get_session_cwd(task_id) or terminal_env("TERMINAL_CWD", "")
    except TerminalPolicyUnavailable:
        return False
    # Normalize path syntax, but do not case-fold or resolve aliases: Windows can
    # host case-sensitive directories even though WindowsPath equality folds case.
    return bool(cwd and Path(cwd).is_absolute() and str(Path(cwd)) == str(Path(planning_home)))


def child_proposal_selection(parent: str, *, child_task_id: str = "") -> ProposalSelection | None:
    """Intersect both served profiles; never union their resource scope."""
    binding = _active.get()
    if binding is None or binding.parent != parent:
        return None
    with _lock:
        if binding.key not in _live:
            return None
    child = _selection(binding.selection.change)
    if child is None:
        return None
    selection = binding.selection.intersect(child)
    if selection is None:
        _unavailable("parent-child-catalog-mismatch")
        return None
    if not _child_targets_planning_home(child_task_id, selection.planning_home):
        _unavailable("child-project-unavailable-or-mismatch")
        return None
    try:
        verify_catalog_metadata(selection)
    except InvalidCatalog as exc:
        _unavailable(str(exc))
        return None
    with _lock:
        return selection if binding.key in _live else None
