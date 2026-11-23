"""Authenticated operator session handoff endpoint (`POST /api/sessions/{session_id}/handoff`).

Isolated from `gateway/session_handoff.py` (which handles cross-platform `/handoff` chat transfers)
and registered via a 1-line `routes.extend(_session_handoff._http_routes(self))` hook in
`APIServerAdapter._http_route_table` to minimize upstream-sync merge surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]

from agent.turn_author import parse_turn_author
from gateway.session import _is_path_unsafe
from gateway.session_contract import AdmissionReceipt, SessionActor, SessionRef, Submission
from hermes_state_runtime import RuntimeStoreError

logger = logging.getLogger("gateway.platforms.api_server")

SCHEMA_VERSION = 1
ALLOWED_INTENTS: frozenset[str] = frozenset({"REQUEST", "REPLY", "FYI"})
TERMINAL_INTENTS: frozenset[str] = frozenset({"REPLY", "FYI"})
HARD_MAX_HOPS = 4
DEFAULT_SAFETY_MODE = "read_only_default"
REDACTED_TOKEN = "<REDACTED_TOKEN>"
MAX_ID_LEN = 255
HANDOFF_CLOSED_CORR_PREFIX = "gateway.api_handoff.closed_corr.v1:"

_ELIGIBLE_LOCAL_SOURCES: frozenset[str] = frozenset(
    {"desktop", "gui", "cli", "tui", "acp", "local", "oneshot", "tool"}
)
_ELIGIBLE_API_SOURCES: frozenset[str] = frozenset({"api_server"})

_BEARER_RE = re.compile(r"(?i)(Bearer\s+)(?!<REDACTED_TOKEN>)[A-Za-z0-9._~+/=-]+")
_KEY_KV_RE = re.compile(
    r"""(?i)((?:AGY_BRIDGE_TOKEN|API_SERVER_KEY|HERMES_API_KEY_[A-Z0-9_]+|BW_SESSION)\s*[:=]\s*)"""
    r"""(?!['"]?vw://)(?!\\"vw://)(?!<REDACTED_TOKEN>)"""
    r"""(?:'[^'\\"\r\n]*'|\\"[^\\"\r\n]*\\"|"[^"\\\r\n]*"|[^\s'",;)}\]\\]+)"""
)
_TRUTHY_STRINGS: frozenset[str] = frozenset({"true", "1", "yes", "on"})


class HandoffError(ValueError):
    """Structured handoff validation or policy error mapping directly to an HTTP response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        err_type: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.err_type = str(err_type)


@dataclass(frozen=True)
class HandoffSender:
    """Validated sender metadata (`from`)."""

    surface: str
    node: str
    author_label: str
    session_id: str | None = None
    profile: str = "default"


@dataclass(frozen=True)
class HandoffTarget:
    """Validated recipient metadata (`to`)."""

    surface: str = "hermes"
    node: str = "asus-vb"
    profile: str = "default"
    session_id: str | None = None


@dataclass(frozen=True)
class HandoffEnvelope:
    """Normalized, immutable cross-surface handoff envelope."""

    schema_version: int
    idempotency_key: str
    correlation_id: str
    intent: str
    hop_count: int
    max_hops: int
    sender: HandoffSender
    target: HandoffTarget
    safety_mode: str
    summary: str
    body: str

    @property
    def is_terminal(self) -> bool:
        return self.intent in TERMINAL_INTENTS


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _is_printable_token(value: str, *, max_len: int = MAX_ID_LEN) -> bool:
    return bool(value) and len(value) <= max_len and all(ch.isprintable() and ch not in "\r\n\x00" for ch in value)


def scrub_secrets(text: str, secrets: Iterable[str] = ()) -> str:
    """Scrub runtime secret values and Bearer/key=value patterns without touching `vw://` refs."""
    if not text:
        return text
    out = text
    ordered = sorted(
        {s for s in secrets if isinstance(s, str) and len(s) >= 4},
        key=len,
        reverse=True,
    )
    for secret in ordered:
        out = out.replace(secret, REDACTED_TOKEN)
    out = _BEARER_RE.sub(rf"\1{REDACTED_TOKEN}", out)
    out = _KEY_KV_RE.sub(rf"\1{REDACTED_TOKEN}", out)
    return out


def scrub_mapping(obj: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively scrub strings inside JSON-serializable structures."""
    if isinstance(obj, str):
        return scrub_secrets(obj, secrets)
    if isinstance(obj, list):
        return [scrub_mapping(item, secrets) for item in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_mapping(item, secrets) for item in obj)
    if isinstance(obj, Mapping):
        return {k: scrub_mapping(v, secrets) for k, v in obj.items()}
    return obj


def _error_json(
    message: str,
    status: int,
    *,
    code: str,
    err_type: str = "invalid_request_error",
    secrets: Iterable[str] = (),
    headers: dict[str, str] | None = None,
) -> web.Response:
    safe_message = scrub_secrets(str(message), secrets)
    payload = {
        "ok": False,
        "code": code,
        "error": {
            "message": safe_message,
            "type": err_type,
            "param": None,
            "code": code,
        },
    }
    return web.json_response(payload, status=status, headers=headers)


def _coerce_bool_toggle(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return False


def is_operator_handoff_enabled(cfg: Mapping[str, Any] | None) -> bool:
    """Evaluate dual opt-in config toggle fail-closed (`gateway.api_server` first, fallback `api_server`)."""
    if not isinstance(cfg, Mapping):
        return False
    from hermes_cli.config import cfg_get

    cfg_dict = dict(cfg)
    primary = cfg_get(cfg_dict, "gateway", "api_server", "allow_operator_handoff", default=None)
    if primary is not None:
        return _coerce_bool_toggle(primary)
    fallback = cfg_get(cfg_dict, "api_server", "allow_operator_handoff", default=False)
    return _coerce_bool_toggle(fallback)


def check_hop_ceiling(env: HandoffEnvelope) -> None:
    """Enforce `hop_count <= max_hops` (where `max_hops` is already clamped `<= HARD_MAX_HOPS`)."""
    if env.hop_count > env.max_hops:
        raise HandoffError(
            429,
            "max_hops_exceeded",
            f"Envelope hop_count ({env.hop_count}) exceeds max_hops ({env.max_hops}).",
            err_type="rate_limit_error",
        )


def parse_handoff_envelope(
    payload: Mapping[str, Any],
    *,
    session_id: str,
    active_profile: str = "default",
    header_idempotency_key: str | None = None,
    secrets: Iterable[str] = (),
    enforce_hop_ceiling: bool = True,
) -> tuple[HandoffEnvelope, dict[str, Any]]:
    """Validate inbound handoff JSON into `(HandoffEnvelope, normalized_turn_author)`."""
    if not isinstance(payload, Mapping):
        raise HandoffError(400, "invalid_envelope", "Envelope payload must be a JSON object.")

    raw_version = payload.get("schema_version", SCHEMA_VERSION)
    if not _is_strict_int(raw_version) or raw_version != SCHEMA_VERSION:
        raise HandoffError(
            400,
            "invalid_schema_version",
            f"Unsupported schema_version: expected {SCHEMA_VERSION}.",
        )

    raw_intent = payload.get("intent")
    if not isinstance(raw_intent, str) or raw_intent.strip() not in ALLOWED_INTENTS:
        raise HandoffError(
            400,
            "invalid_intent",
            "Envelope intent must be one of REQUEST, REPLY, or FYI.",
        )
    intent = raw_intent.strip()

    body_idem = _clean_optional_str(payload.get("idempotency_key"))
    hdr_idem = _clean_optional_str(header_idempotency_key)
    if body_idem and hdr_idem and body_idem != hdr_idem:
        raise HandoffError(
            400,
            "invalid_idempotency_key",
            "Header Idempotency-Key does not match body idempotency_key.",
        )
    idempotency_key = body_idem or hdr_idem
    if not idempotency_key or not _is_printable_token(idempotency_key):
        raise HandoffError(
            400,
            "invalid_idempotency_key",
            "Envelope requires a non-empty printable idempotency_key (<= 255 chars).",
        )

    correlation_id = _clean_optional_str(payload.get("correlation_id"))
    if not correlation_id or not _is_printable_token(correlation_id):
        raise HandoffError(
            400,
            "invalid_correlation_id",
            "Envelope requires a non-empty printable correlation_id (<= 255 chars).",
        )

    raw_from = payload.get("from")
    if not isinstance(raw_from, Mapping):
        raise HandoffError(
            400,
            "invalid_turn_author",
            "Envelope requires a 'from' object with non-empty node and author_label.",
        )
    from_node = _clean_optional_str(raw_from.get("node"))
    from_author = _clean_optional_str(raw_from.get("author_label"))
    if not from_node or not from_author:
        raise HandoffError(
            400,
            "invalid_turn_author",
            "Envelope requires non-empty from.node and from.author_label.",
        )
    from_surface = _clean_optional_str(raw_from.get("surface")) or "antigravity"
    from_session_id = _clean_optional_str(raw_from.get("session_id"))
    from_profile = _clean_optional_str(raw_from.get("profile")) or "default"

    safe_author_label = scrub_secrets(from_author, secrets)
    candidate_turn_author = {
        "id": f"{from_surface}:{from_node}",
        "name": safe_author_label,
        "is_bot": True,
    }
    if "turn_author" in payload and payload["turn_author"] is not None:
        if parse_turn_author(payload["turn_author"]) is None:
            raise HandoffError(
                400,
                "invalid_turn_author",
                "Explicit turn_author field failed parse_turn_author validation.",
            )
    normalized_author = parse_turn_author(candidate_turn_author)
    if (
        normalized_author is None
        or not normalized_author.get("id")
        or not normalized_author.get("name")
    ):
        raise HandoffError(
            400,
            "invalid_turn_author",
            "Envelope sender failed non-empty parse_turn_author validation.",
        )

    sender = HandoffSender(
        surface=from_surface,
        node=from_node,
        author_label=safe_author_label,
        session_id=from_session_id,
        profile=from_profile,
    )

    raw_to = payload.get("to")
    if raw_to is None:
        raw_to = {}
    if not isinstance(raw_to, Mapping):
        raise HandoffError(
            400, "invalid_target", "Envelope 'to' field must be an object when present."
        )
    to_session_id = _clean_optional_str(raw_to.get("session_id"))
    if to_session_id is not None and to_session_id != session_id:
        raise HandoffError(
            400,
            "invalid_target",
            "Envelope to.session_id does not match target URL session_id.",
        )
    target = HandoffTarget(
        surface=_clean_optional_str(raw_to.get("surface")) or "hermes",
        node=_clean_optional_str(raw_to.get("node")) or "asus-vb",
        profile=_clean_optional_str(raw_to.get("profile")) or active_profile or "default",
        session_id=session_id,
    )

    raw_hop = payload.get("hop_count")
    if not _is_strict_int(raw_hop) or raw_hop < 1:
        raise HandoffError(
            400, "invalid_hop_count", "Envelope hop_count must be an integer >= 1."
        )

    raw_max_hops = payload.get("max_hops", HARD_MAX_HOPS)
    if not _is_strict_int(raw_max_hops) or raw_max_hops < 1:
        raise HandoffError(
            400, "invalid_max_hops", "Envelope max_hops must be an integer >= 1."
        )
    clamped_max_hops = min(raw_max_hops, HARD_MAX_HOPS)

    raw_safety = payload.get("safety_mode")
    if raw_safety is None:
        safety_mode = DEFAULT_SAFETY_MODE
    elif isinstance(raw_safety, str) and raw_safety.strip():
        safety_mode = raw_safety.strip()
    else:
        raise HandoffError(
            400, "invalid_safety_mode", "Envelope safety_mode must be a non-empty string."
        )

    raw_summary = payload.get("summary")
    if not isinstance(raw_summary, str) or not raw_summary.strip():
        raise HandoffError(400, "invalid_summary", "Envelope requires a non-empty summary.")
    summary = raw_summary.strip()

    raw_body = payload.get("body")
    if not isinstance(raw_body, str) or not raw_body.strip():
        raise HandoffError(400, "invalid_body", "Envelope requires a non-empty body.")
    body = raw_body

    env = HandoffEnvelope(
        schema_version=SCHEMA_VERSION,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        intent=intent,
        hop_count=raw_hop,
        max_hops=clamped_max_hops,
        sender=sender,
        target=target,
        safety_mode=safety_mode,
        summary=summary,
        body=body,
    )
    if enforce_hop_ceiling:
        check_hop_ceiling(env)
    return env, normalized_author


def render_envelope_prompt(env: HandoffEnvelope, *, secrets: Iterable[str] = ()) -> str:
    """Render a deterministic, secret-scrubbed chat prompt from a validated envelope.

    Pure function of `(env, secrets)`: zero wall-clock timestamps or random IDs so
    `SessionAuthority` payload-digest checks match byte-for-byte on `request_id` replay.
    """
    sender_session = env.sender.session_id or "unspecified"
    target_session = env.target.session_id or "unspecified"
    safe_author = scrub_secrets(env.sender.author_label, secrets)
    safe_summary = scrub_secrets(env.summary, secrets)
    safe_body = scrub_secrets(env.body, secrets)

    if env.intent == "REQUEST":
        next_hop = env.hop_count + 1
        directive = (
            f"Reply expected -> send REPLY with correlation_id={env.correlation_id}, "
            f"hop_count={next_hop} back to sender once processed; stay read-only/advisory "
            f"unless your local user has authorized writes/installs/commits."
        )
    else:
        directive = (
            f"Terminal update (intent={env.intent}) -> summarize/inform your local user and STOP. "
            f"Do NOT send or echo a message back across the bridge."
        )

    lines = [
        "[HERMES <-> ANTIGRAVITY SESSION BRIDGE HANDOFF]",
        f"Intent: {env.intent} | Correlation-ID: {env.correlation_id} | Hop: {env.hop_count}/{env.max_hops}",
        f"From: {safe_author} ({env.sender.surface}:{env.sender.node}, session={sender_session})",
        f"To: {env.target.surface}:{env.target.node} (profile={env.target.profile}, session={target_session})",
        (
            f"Safety-Mode: {env.safety_mode} — Stay read-only/advisory by default; pause for explicit "
            f"local user authorization before modifying files, installing packages, writing databases, "
            f"or committing."
        ),
        f"Directive: {directive}",
        f"Summary: {safe_summary}",
    ]
    if safe_body.strip():
        lines.append("")
        lines.append(safe_body)
    return "\n".join(lines)


def _resolve_authority(adapter: Any) -> Any:
    injected = getattr(adapter, "_handoff_authority", None)
    if injected is not None:
        return injected
    runner = getattr(adapter, "gateway_runner", None)
    if runner is None:
        return None
    from gateway.session_authorities import active_authority

    return active_authority(runner)


def _active_profile_label() -> str:
    try:
        from gateway.platforms.api_server import _api_request_profile
        from gateway.session_authorities import served_profile_name
        from hermes_constants import get_hermes_home

        return _api_request_profile.get() or served_profile_name(get_hermes_home())
    except Exception:
        return "default"


def _check_correlation_and_existing_admission(
    authority: Any,
    *,
    correlation_id: str,
    idempotency_key: str,
    request_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Read durable closed-correlation record and whether `request_id` was already admitted."""
    db = getattr(authority, "db", None)
    if db is None:
        closed_mem = getattr(authority, "_handoff_closed_correlations", {})
        admitted_mem = getattr(authority, "_handoff_admitted_keys", {})
        return closed_mem.get(correlation_id), request_id in admitted_mem

    meta_key = HANDOFF_CLOSED_CORR_PREFIX + correlation_id
    with db._read_ctx() as conn:
        raw_closed = conn.execute(
            "SELECT value FROM state_meta WHERE key=?", (meta_key,)
        ).fetchone()
        existing_adm = conn.execute(
            "SELECT 1 FROM session_admissions WHERE principal_id=? AND request_id=? LIMIT 1",
            ("api-operator", request_id),
        ).fetchone()

    closed_doc: dict[str, Any] | None = None
    if raw_closed is not None:
        try:
            parsed = json.loads(raw_closed[0])
            closed_doc = parsed if isinstance(parsed, dict) else {"correlation_id": correlation_id}
        except (TypeError, ValueError):
            closed_doc = {"correlation_id": correlation_id}
    return closed_doc, existing_adm is not None


def _persist_closed_correlation(
    authority: Any,
    *,
    correlation_id: str,
    intent: str,
    idempotency_key: str,
    session_id: str,
) -> None:
    record = {
        "correlation_id": correlation_id,
        "closed_by_intent": intent,
        "idempotency_key": idempotency_key,
        "session_id": session_id,
        "closed_at": time.time(),
    }
    db = getattr(authority, "db", None)
    if db is None:
        closed_mem = getattr(authority, "_handoff_closed_correlations", None)
        if closed_mem is None:
            closed_mem = {}
            setattr(authority, "_handoff_closed_correlations", closed_mem)
        closed_mem.setdefault(correlation_id, record)
        return

    meta_key = HANDOFF_CLOSED_CORR_PREFIX + correlation_id
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _write(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO state_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
            (meta_key, encoded),
        )

    db._execute_write(_write)


def _inspect_session_eligibility(
    authority: Any,
    session_id: str,
    request_id: str,
) -> tuple[str, bool]:
    """Validate target session eligibility against `authority.db` without mutating rows.

    Returns `(owner_session_id, needs_api_bind)`. Raises `HandoffError` on refusal.
    """
    db = getattr(authority, "db", None)
    if db is None:
        return session_id, False

    from gateway.session_history_mount import is_history_mount
    from hermes_state_local import API_BINDING_PREFIX, POLICY_PREFIX, local_lineage_owner
    from hermes_state_local_migration import LEGACY_PREFIX
    from hermes_state_mutation_retirement import retired_in

    logical_fn = getattr(authority, "logical_owner", None)
    logical_root = logical_fn(session_id) if callable(logical_fn) else session_id

    with db._read_ctx() as conn:
        if retired_in(conn, session_id):
            raise HandoffError(404, "not_found", f"Session not found: {session_id}")
        raw_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if raw_row is None:
            raise HandoffError(404, "not_found", f"Session not found: {session_id}")
        row = dict(raw_row)
        if row.get("end_reason") == "deleted":
            raise HandoffError(404, "not_found", f"Session not found: {session_id}")

        owner_id = local_lineage_owner(conn, logical_root or session_id)
        if owner_id != session_id:
            if retired_in(conn, owner_id):
                raise HandoffError(404, "not_found", f"Session not found: {session_id}")
            raw_owner = conn.execute("SELECT * FROM sessions WHERE id=?", (owner_id,)).fetchone()
            if raw_owner is None:
                raise HandoffError(404, "not_found", f"Session not found: {session_id}")
            owner_row = dict(raw_owner)
        else:
            owner_row = row

        own_profile = db._own_profile_name() or "default"
        if owner_row.get("profile_name") not in (None, "", own_profile):
            raise HandoffError(404, "not_found", f"Session not found: {session_id}")

        if bool(row.get("archived")) or bool(owner_row.get("archived")):
            raise HandoffError(
                409,
                "session_not_handoff_eligible",
                f"Session {session_id} is archived and not eligible for operator handoff.",
            )

        source = str(owner_row.get("source") or row.get("source") or "").strip().lower()
        if source not in (_ELIGIBLE_LOCAL_SOURCES | _ELIGIBLE_API_SOURCES):
            raise HandoffError(
                409,
                "session_not_handoff_eligible",
                f"Session source {source!r} is not eligible for operator handoff.",
            )

        other_target = conn.execute(
            "SELECT 1 FROM session_admissions WHERE principal_id=? AND request_id=? AND target_session_id!=? LIMIT 1",
            ("api-operator", request_id, owner_id),
        ).fetchone()
        if other_target is not None:
            raise HandoffError(
                409,
                "admission_conflict",
                "idempotency_key was already used for another target session.",
            )

        live_sessions = getattr(authority, "sessions", {}) or {}
        live_entry = live_sessions.get(owner_id)
        has_bound_live = live_entry is not None and not is_history_mount(live_entry)

        if source in _ELIGIBLE_LOCAL_SOURCES:
            policy_meta = conn.execute(
                "SELECT value FROM state_meta WHERE key=?",
                (POLICY_PREFIX + owner_id,),
            ).fetchone()
            legacy_meta = conn.execute(
                "SELECT 1 FROM state_meta WHERE key=?",
                (LEGACY_PREFIX + owner_id,),
            ).fetchone()
            if policy_meta is not None:
                try:
                    parsed_policy = json.loads(policy_meta[0])
                    pol_src = (
                        str((parsed_policy.get("policy") or {}).get("source") or "")
                        .strip()
                        .lower()
                    )
                except (TypeError, ValueError, AttributeError):
                    pol_src = ""
                if pol_src in {"cron", "bot_room"}:
                    raise HandoffError(
                        409,
                        "session_not_handoff_eligible",
                        f"Session source {pol_src!r} is not eligible for operator handoff.",
                    )
            if policy_meta is None and legacy_meta is None and not has_bound_live:
                raise HandoffError(
                    409,
                    "legacy_session_unbound",
                    f"Legacy session {session_id} has no native owner binding; open it locally first.",
                )
            return owner_id, False

        # source == 'api_server'
        api_meta = conn.execute(
            "SELECT value FROM state_meta WHERE key=?",
            (API_BINDING_PREFIX + owner_id,),
        ).fetchone()
        if api_meta is not None:
            try:
                binding = json.loads(api_meta[0])
            except (TypeError, ValueError):
                binding = {}
            if isinstance(binding, dict) and binding.get("storage_source") == "bot_room":
                raise HandoffError(
                    409,
                    "session_not_handoff_eligible",
                    "bot_room sessions are not eligible for operator handoff.",
                )
            return owner_id, False

        if has_bound_live:
            return owner_id, False
        if owner_row.get("session_key") not in (None, ""):
            raise HandoffError(
                409,
                "session_not_handoff_eligible",
                f"API session {session_id} carries a foreign route key and cannot be bound.",
            )
        return owner_id, True


def _compute_queue_position(authority: Any, owner_id: str, receipt: AdmissionReceipt) -> int:
    if receipt.status != "queued":
        return 0
    db = getattr(authority, "db", None)
    if db is None:
        return 1
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM session_admissions WHERE target_session_id=? AND status='queued' AND seq<=?",
            (owner_id, receipt.sequence),
        ).fetchone()
    count = int(row[0]) if row is not None else 1
    return max(1, count)


def _map_runtime_store_error(exc: RuntimeStoreError, *, secrets: Iterable[str] = ()) -> web.Response:
    reason = str(exc.reason or "admission_conflict")
    status_map = {
        "not_found": 404,
        "invalid_params": 400,
        "runtime_draining": 503,
        "runtime_retired": 503,
        "storage_unavailable": 503,
    }
    status = status_map.get(reason, 409)
    err_type = "server_error" if status >= 500 else "invalid_request_error"
    headers = {"Retry-After": "1"} if status == 503 else None
    detail = f"{reason}: {exc.detail}" if getattr(exc, "detail", None) else reason
    return _error_json(detail, status, code=reason, err_type=err_type, secrets=secrets, headers=headers)


async def _submit_with_replay_tracking(
    authority: Any,
    actor: SessionActor,
    submission: Submission,
) -> tuple[AdmissionReceipt, bool]:
    """Submit to `SessionAuthority` while atomically detecting exact `request_id` replays."""
    params = inspect.signature(authority.submit).parameters
    if "_authorize_write" in params and "_replay_guard" in params:
        replayed_box = [False]

        def _on_new(_conn: Any) -> None:
            replayed_box[0] = False

        def _on_replay(_conn: Any) -> None:
            replayed_box[0] = True

        receipt = await authority.submit(
            actor,
            submission,
            _authorize_write=_on_new,
            _replay_guard=_on_replay,
        )
        return receipt, replayed_box[0]

    # Fallback for minimal 2-arg protocol test doubles without SQLite hooks
    admitted_mem: dict[str, tuple[str, str, AdmissionReceipt]] = getattr(
        authority, "_handoff_admitted_keys", None
    )
    if admitted_mem is None:
        admitted_mem = {}
        setattr(authority, "_handoff_admitted_keys", admitted_mem)

    payload_fp = hashlib.sha256(
        json.dumps(dict(submission.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    existing = admitted_mem.get(submission.request_id)
    if existing is not None:
        prev_sid, prev_fp, prev_receipt = existing
        if prev_sid != submission.ref.session_id or prev_fp != payload_fp:
            raise RuntimeStoreError("admission_conflict")
        return prev_receipt, True

    receipt = await authority.submit(actor, submission)
    admitted_mem[submission.request_id] = (submission.ref.session_id, payload_fp, receipt)
    return receipt, False


async def handle_session_handoff(
    adapter: Any,
    request: web.Request,
    *,
    authority: Any = None,
    config_loader: Callable[[], Mapping[str, Any]] | None = None,
) -> web.Response:
    """`POST /api/sessions/{session_id}/handoff` handler."""
    # 1. Explicit API_SERVER_KEY authentication FIRST (before config check or body parse)
    auth_err = adapter._check_auth(request)
    if auth_err is not None:
        return auth_err
    expected_key_fn = getattr(adapter, "_expected_api_key", None)
    expected_key = expected_key_fn() if callable(expected_key_fn) else ""
    if callable(expected_key_fn) and not expected_key:
        auth_failed_fn = getattr(adapter, "_auth_failed_response", None)
        if callable(auth_failed_fn):
            return auth_failed_fn()
        return _error_json(
            "Invalid gateway API key (API_SERVER_KEY)",
            401,
            code="gateway_auth_failed",
            err_type="gateway_auth_error",
        )

    secrets: tuple[str, ...] = (expected_key,) if expected_key else ()

    # 2. Dual opt-in config toggle fail-closed
    loader = config_loader or getattr(adapter, "_handoff_config_loader", None)
    if loader is None:
        from hermes_cli.config import load_config as loader
    try:
        cfg = loader()
    except Exception:
        cfg = None
    if not is_operator_handoff_enabled(cfg):
        return _error_json(
            "Operator session handoff is disabled; set gateway.api_server.allow_operator_handoff: true to enable.",
            403,
            code="handoff_disabled",
            err_type="permission_error",
            secrets=secrets,
        )

    # 3. Validate URL session_id & parse JSON body
    session_id = str(request.match_info.get("session_id") or "").strip()
    if (
        not session_id
        or len(session_id) > 256
        or re.search(r"[\r\n\x00]", session_id)
        or _is_path_unsafe(session_id)
    ):
        return _error_json(
            "Invalid session ID",
            400,
            code="invalid_session_id",
            secrets=secrets,
        )

    try:
        raw_body = await request.json()
    except Exception:
        return _error_json(
            "Invalid JSON in request body",
            400,
            code="invalid_json",
            secrets=secrets,
        )
    if not isinstance(raw_body, Mapping):
        return _error_json(
            "Request body must be a JSON object",
            400,
            code="invalid_envelope",
            secrets=secrets,
        )

    # 4. Validate envelope schema, intent, idempotency_key, correlation_id, and turn_author
    active_profile = _active_profile_label()
    try:
        env, normalized_author = parse_handoff_envelope(
            raw_body,
            session_id=session_id,
            active_profile=active_profile,
            header_idempotency_key=request.headers.get("Idempotency-Key"),
            secrets=secrets,
            enforce_hop_ceiling=False,
        )
    except HandoffError as exc:
        return _error_json(
            exc.message,
            exc.status_code,
            code=exc.code,
            err_type=exc.err_type,
            secrets=secrets,
        )

    resolved_authority = authority if authority is not None else _resolve_authority(adapter)
    request_id = f"handoff:{env.idempotency_key}"

    # 5. Durable correlation closure check BEFORE hop ceiling (so follow-ups after a closed
    #    chain return 409 correlation_closed even at hop_count > max_hops, while exact retries pass)
    if resolved_authority is not None:
        try:
            closed_doc, already_admitted = await asyncio.to_thread(
                _check_correlation_and_existing_admission,
                resolved_authority,
                correlation_id=env.correlation_id,
                idempotency_key=env.idempotency_key,
                request_id=request_id,
            )
        except (sqlite3.Error, OSError) as exc:
            return _error_json(
                f"Session store unavailable: {type(exc).__name__}",
                503,
                code="storage_unavailable",
                err_type="server_error",
                secrets=secrets,
                headers={"Retry-After": "1"},
            )
        if (
            closed_doc is not None
            and closed_doc.get("idempotency_key") != env.idempotency_key
            and not already_admitted
        ):
            return _error_json(
                f"Correlation chain {env.correlation_id!r} is already closed by a terminal REPLY/FYI.",
                409,
                code="correlation_closed",
                secrets=secrets,
            )

    # 6. Enforce hop ceiling (`max_hops` hard-clamped <= 4; `hop_count > max_hops` -> 429)
    try:
        check_hop_ceiling(env)
    except HandoffError as exc:
        return _error_json(
            exc.message,
            exc.status_code,
            code=exc.code,
            err_type=exc.err_type,
            secrets=secrets,
        )

    if resolved_authority is None:
        return _error_json(
            "SessionAuthority is unavailable on this listener",
            503,
            code="session_authority_unavailable",
            err_type="server_error",
            secrets=secrets,
            headers={"Retry-After": "1"},
        )

    # 7. Enforce session eligibility against `authority.db` (`404` / `409`)
    try:
        owner_id, needs_api_bind = await asyncio.to_thread(
            _inspect_session_eligibility,
            resolved_authority,
            session_id,
            request_id,
        )
    except HandoffError as exc:
        return _error_json(
            exc.message,
            exc.status_code,
            code=exc.code,
            err_type=exc.err_type,
            secrets=secrets,
        )
    except (sqlite3.Error, OSError) as exc:
        return _error_json(
            f"Session store unavailable: {type(exc).__name__}",
            503,
            code="storage_unavailable",
            err_type="server_error",
            secrets=secrets,
            headers={"Retry-After": "1"},
        )

    # 8. Deterministic prompt render & SessionAuthority FIFO submission
    rendered_text = render_envelope_prompt(env, secrets=secrets)
    profile_id = str(getattr(resolved_authority, "profile_id", "default"))
    actor = SessionActor(
        "api-operator",
        profile_id,
        frozenset({"session:read", "session:submit", "session:operator"}),
        f"api-handoff:{env.idempotency_key}",
    )
    submission = Submission(
        request_id=request_id,
        ref=SessionRef(profile_id, owner_id),
        intent="queue",
        payload={
            "text": rendered_text,
            "turn_author": normalized_author,
        },
    )

    try:
        if needs_api_bind:
            from gateway.session_api import bind_api_steps, run_steps_off_loop

            await run_steps_off_loop(
                resolved_authority, bind_api_steps(resolved_authority, owner_id)
            )
        receipt, replayed = await _submit_with_replay_tracking(
            resolved_authority, actor, submission
        )
        if env.is_terminal:
            await asyncio.to_thread(
                _persist_closed_correlation,
                resolved_authority,
                correlation_id=env.correlation_id,
                intent=env.intent,
                idempotency_key=env.idempotency_key,
                session_id=owner_id,
            )
        queue_position = await asyncio.to_thread(
            _compute_queue_position, resolved_authority, owner_id, receipt
        )
    except RuntimeStoreError as exc:
        return _map_runtime_store_error(exc, secrets=secrets)
    except (sqlite3.Error, OSError) as exc:
        return _error_json(
            f"Session store unavailable: {type(exc).__name__}",
            503,
            code="storage_unavailable",
            err_type="server_error",
            secrets=secrets,
            headers={"Retry-After": "1"},
        )

    # HX017: never log user body, summary, or display names at INFO+
    logger.debug(
        "Operator handoff admitted session=%s admission=%s intent=%s hop=%d/%d replayed=%s body_len=%d",
        session_id,
        receipt.admission_id,
        env.intent,
        env.hop_count,
        env.max_hops,
        replayed,
        len(env.body),
    )

    response_payload = scrub_mapping(
        {
            "ok": True,
            "admission_id": receipt.admission_id,
            "session_id": session_id,
            "queue_position": queue_position,
            "status": receipt.status,
            "correlation_id": env.correlation_id,
            "idempotency_key": env.idempotency_key,
            "intent": env.intent,
            "hop_count": env.hop_count,
            "max_hops": env.max_hops,
            "correlation_closed": env.is_terminal,
            "replayed": replayed,
        },
        secrets,
    )
    headers = {"Idempotency-Replayed": "true"} if replayed else None
    return web.json_response(response_payload, status=202, headers=headers)


def _handoff_route(adapter: Any):
    async def _handler(request: web.Request) -> web.Response:
        return await handle_session_handoff(adapter, request)

    return _handler


def _http_routes(adapter: Any) -> list[tuple[str, str, Any]]:
    return [
        ("POST", "/api/sessions/{session_id}/handoff", _handoff_route(adapter)),
    ]
