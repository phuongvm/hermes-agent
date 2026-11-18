"""In-process provenance for an explicit, admitted proposal command. No tool grants."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from weakref import ref

from agent.prerequisite_catalog import (
    WORKFLOW, InvalidCatalog, ProposalSelection, freeze_proposal_catalog, proposal_entries, valid_change_id,
    verify_catalog_metadata,
)

logger = logging.getLogger(__name__)


def _unavailable(reason: str) -> None:
    logger.info("PREREQUISITE_SELECTION_UNAVAILABLE: %s", reason)


def _selection(change: str) -> ProposalSelection | None:
    from hermes_cli.config import load_config_readonly

    try:
        entry = proposal_entries(load_config_readonly().get("prerequisite_catalog")).get(change)
        return freeze_proposal_catalog(change, entry)
    except InvalidCatalog as exc:
        _unavailable(str(exc))
    except (AttributeError, OSError, ValueError):
        _unavailable("profile-config-unavailable")
    return None  # Invalid or absent catalog never changes ordinary controls.


@dataclass(frozen=True)
class _Command:
    event: ref
    route: str
    selection: ProposalSelection


def note_proposal_command(event, route, skill, argument):
    """Called after successful skill resolution on the authorized inbound command path."""
    if (skill != WORKFLOW or event.internal or not event.allow_gateway_control or not route
            or not valid_change_id(argument)):
        return
    selection = _selection(argument)
    if selection is not None:
        event._proposal_command = _Command(ref(event), route, selection)


_QUEUED_LIMIT = 128
_QUEUED_SECONDS = 120


def queue_proposal_command(authority, event, row) -> None:
    """Transfer an admitted command to the process-local FIFO sidecar."""
    command = vars(event).pop("_proposal_command", None)
    payload = row.get("payload", {})
    native = payload.get("native_text_v1", {})
    text = payload.get("text")
    if (not row.get("_created_this_write") or row.get("status") != "queued"
            or type(command) is not _Command or command.event() is not event
            or command.route != native.get("route") or not isinstance(text, str) or text != event.text):
        return
    now = time.monotonic()
    pending = authority.proposal_commands
    for key, item in tuple(pending.items()):
        if item[0] <= now:
            del pending[key]
    if len(pending) < _QUEUED_LIMIT:
        pending[row["admission_id"]] = (
            now + _QUEUED_SECONDS, row["target_session_id"], command.route,
            sha256(text.encode("utf-8", "surrogatepass")).digest(), command.selection,
        )


def restore_proposal_command(ticket, event, route, session_id, row) -> None:
    if ticket is None or event.internal or not event.allow_gateway_control:
        return
    expires, session, queued_route, digest, selection = ticket
    if (time.monotonic() < expires and row.get("status") == "started"
            and (session, queued_route) == (session_id, route) and isinstance(event.text, str)
            and digest == sha256(event.text.encode("utf-8", "surrogatepass")).digest()):
        event._proposal_command = _Command(ref(event), route, selection)


_active: ContextVar[tuple[object, str, ProposalSelection] | None] = ContextVar(
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
        binding = (key, parent, command.selection)
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
            _live.discard(binding[0])
        _active.set(None)


def child_proposal_selection(parent: str) -> ProposalSelection | None:
    """Intersect both served profiles; never union their resource scope."""
    binding = _active.get()
    if binding is None or binding[1] != parent:
        return None
    key, _, selection = binding
    with _lock:
        if key not in _live:
            return None
    child = _selection(selection.change)
    if child is None:
        return None
    selection = selection.intersect(child)
    if selection is None:
        _unavailable("parent-child-catalog-mismatch")
        return None
    try:
        verify_catalog_metadata(selection)
    except InvalidCatalog as exc:
        _unavailable(str(exc))
        return None
    with _lock:
        return selection if key in _live else None
