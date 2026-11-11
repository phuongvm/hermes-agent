"""Dataclasses, enums, exceptions, and schemas for WorkspaceLeaseCoordinator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import math
from typing import Any, Mapping

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DEFERRED = 75
EXIT_PREEMPTED = 78
EXIT_DATAERR = 79
EX_TEMPFAIL = EXIT_DEFERRED
EX_DATAERR = EXIT_DATAERR

DEFAULT_HEARTBEAT_INTERVAL_S = 30.0
DEFAULT_HEARTBEAT_WINDOW_S = 90.0
MAX_LEASE_TTL_S = 1800.0
DEFAULT_MAX_TTL_S = MAX_LEASE_TTL_S
ACTIVE_SESSION_WINDOW_S = 180.0

VALID_NAMESPACES = frozenset({"repo", "profile", "catalog"})
VALID_MODES = frozenset({"shared_session", "exclusive_cutover"})
LEASE_STATES = frozenset(
    {"UNLOCKED", "ACQUIRING", "ACQUIRED", "RELEASING", "EXPIRED", "PREEMPTED"}
)
JOURNAL_STATES = frozenset(
    {
        "PUBLISH_INTENT",
        "PUBLISHED_LEDGER_PENDING",
        "COMMITTED",
        "ROLLED_BACK",
        "ABORTED",
    }
)


def normalize_scope(raw: str) -> str:
    """Normalize a raw lease scope into `<namespace>:<target>` canonical form."""
    if not isinstance(raw, str):
        raise ValueError(f"Lease scope must be a string, got {type(raw).__name__}")
    cleaned = raw.strip().replace("\\", "/")
    if ":" not in cleaned:
        raise ValueError(
            f"Invalid lease scope {raw!r}: expected '<namespace>:<target>' "
            f"with namespace in {sorted(VALID_NAMESPACES)}"
        )
    ns_raw, tgt_raw = cleaned.split(":", 1)
    namespace = ns_raw.strip().lower()
    if namespace not in VALID_NAMESPACES:
        raise ValueError(
            f"Invalid lease scope namespace {namespace!r} in {raw!r}: "
            f"expected one of {sorted(VALID_NAMESPACES)}"
        )
    target = tgt_raw.strip().strip("/")
    while "//" in target:
        target = target.replace("//", "/")
    if not target:
        raise ValueError(f"Invalid lease scope {raw!r}: target cannot be empty")
    return f"{namespace}:{target.lower()}"


def scopes_overlap(scope_a: str, scope_b: str) -> bool:
    """Return True if two lease scopes conflict hierarchically or via wildcard."""
    norm_a = normalize_scope(scope_a)
    norm_b = normalize_scope(scope_b)
    ns_a, tgt_a = norm_a.split(":", 1)
    ns_b, tgt_b = norm_b.split(":", 1)
    if ns_a != ns_b:
        return False
    if tgt_a == tgt_b or tgt_a == "*" or tgt_b == "*":
        return True
    if tgt_a.startswith(tgt_b + "/") or tgt_b.startswith(tgt_a + "/"):
        return True
    return False


def _token_to_slug(fencing_token: str) -> str:
    """Convert a fencing token into a filesystem-safe suffix slug."""
    return (
        str(fencing_token)
        .strip()
        .replace("@", "_")
        .replace(":", "_")
        .replace("*", "star")
        .replace("/", "_")
        .replace("\\", "_")
    )


token_to_slug = _token_to_slug


def _format_holder_id(
    holder_pid: int, process_start_time_ns: int, holder: str
) -> str:
    """Format a durable per-holder identity string that survives JSON/CLI round-trips."""
    return f"{int(holder_pid)}:{int(process_start_time_ns)}:{holder!s}"


def _split_token_and_holder_id(fencing_token: str) -> tuple[str, str | None]:
    """Split `<base_token>[@<holder_id>]` into `(base_token, holder_id | None)`."""
    raw = str(fencing_token).strip()
    if "@" in raw:
        base, holder_id = raw.split("@", 1)
        return base.strip(), (holder_id.strip() if holder_id.strip() else None)
    return raw, None


def _clamp_ttl_s(ttl_s: float | None) -> float:
    """Enforce the hard `MAX_LEASE_TTL_S = 1800.0` ceiling on lease TTL."""
    if ttl_s is None:
        return MAX_LEASE_TTL_S
    val = float(ttl_s or 60.0)
    if math.isnan(val) or val < 0.0:
        raise ValueError(f"Invalid lease ttl_s: {ttl_s!r}")
    if math.isinf(val):
        return MAX_LEASE_TTL_S
    return min(max(val, 1.0), MAX_LEASE_TTL_S)


def _extract_scope_from_token(fencing_token: str) -> str:
    """Extract and normalize the `<namespace>:<target>` scope prefix from a fencing token."""
    raw, _ = _split_token_and_holder_id(fencing_token)
    parts = raw.rsplit(":", 2)
    if len(parts) < 3:
        raise ValueError(f"Cannot infer scope from fencing token {fencing_token!r}")
    return normalize_scope(parts[0])


class _LeaseTokenStr(str):
    """String fencing token carrying optional holder identity for shared-session release."""

    _holder_id: str | None = None
    _holder_pid: int | None = None
    _holder: str | None = None


class _LeaseTokenMap(dict[str, str]):
    """Fencing token mapping carrying optional holder identity for shared-session release."""

    _holder_id: str | None = None
    _holder_pid: int | None = None
    _holder: str | None = None


def _make_token_str(
    token: str,
    *,
    holder_id: str | None = None,
    holder_pid: int | None = None,
    holder: str | None = None,
) -> _LeaseTokenStr:
    wrapped = _LeaseTokenStr(token)
    wrapped._holder_id = holder_id
    wrapped._holder_pid = holder_pid
    wrapped._holder = holder
    return wrapped


def _make_token_map(
    tokens: Mapping[str, str],
    *,
    holder_id: str | None = None,
    holder_pid: int | None = None,
    holder: str | None = None,
) -> _LeaseTokenMap:
    wrapped = _LeaseTokenMap(
        {
            k: _make_token_str(
                v, holder_id=holder_id, holder_pid=holder_pid, holder=holder
            )
            for k, v in tokens.items()
        }
    )
    wrapped._holder_id = holder_id
    wrapped._holder_pid = holder_pid
    wrapped._holder = holder
    return wrapped


class LeasePreemptedError(RuntimeError):
    """Raised when a stale or expired fencing token attempts to commit or renew."""

    def __init__(
        self,
        message: str,
        *,
        fencing_token: str = "",
        staged_path: str = "",
        reason_code: str = "STALE_FENCING_TOKEN",
        exit_code: int = EXIT_PREEMPTED,
    ) -> None:
        super().__init__(message)
        self.fencing_token = fencing_token
        self.staged_path = staged_path
        self.reason_code = reason_code
        self.exit_code = exit_code


class CorruptPersistentStateError(RuntimeError):
    """Raised when `workspace_leases.json` or journal contains malformed/corrupt state."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        reason_code: str = "CORRUPT_PERSISTENT_STATE",
        exit_code: int = EXIT_DATAERR,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.reason_code = reason_code
        self.exit_code = exit_code


class CorruptJournalError(CorruptPersistentStateError):
    """Raised when `workspace_lease.journal.json` contains malformed/corrupt JSON state."""


@dataclass
class LeaseRecord:
    """Active or historical lease record stored in `workspace_leases.json`."""

    scope: str = "profile:*"
    state: str = "ACQUIRED"
    mode: str = "exclusive_cutover"
    holder: str = "cli"
    holder_pid: int = 0
    process_start_time_ns: int = 0
    fencing_token: str = ""
    epoch: int = 0
    generation: int = 1
    acquired_at_epoch: float = 0.0
    last_heartbeat_epoch: float = 0.0
    expires_at_epoch: float = 0.0
    ttl_s: float = MAX_LEASE_TTL_S
    max_expires_at: float = 0.0
    last_committed_digest: str = ""
    shared_holders: list[dict[str, Any]] = field(default_factory=list)

    @property
    def base_token(self) -> str:
        base, _ = _split_token_and_holder_id(self.fencing_token)
        return base

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LeaseRecord:
        acquired_at = float(data.get("acquired_at_epoch", 0.0))
        ttl_s = _clamp_ttl_s(data.get("ttl_s", MAX_LEASE_TTL_S))
        hard_ceiling = (
            min(acquired_at + ttl_s, acquired_at + MAX_LEASE_TTL_S)
            if acquired_at > 0.0
            else float(data.get("max_expires_at", 0.0))
        )
        raw_max_exp = float(data.get("max_expires_at", 0.0) or hard_ceiling)
        max_expires_at = (
            min(raw_max_exp, hard_ceiling) if acquired_at > 0.0 else raw_max_exp
        )
        raw_expires = float(data.get("expires_at_epoch", 0.0))
        expires_at = (
            min(raw_expires, max_expires_at)
            if max_expires_at > 0.0
            else raw_expires
        )
        return cls(
            scope=str(data.get("scope", "profile:*")),
            state=str(data.get("state", "ACQUIRED")),
            mode=str(data.get("mode", "exclusive_cutover")),
            holder=str(data.get("holder", "cli")),
            holder_pid=int(data.get("holder_pid", 0)),
            process_start_time_ns=int(data.get("process_start_time_ns", 0)),
            fencing_token=str(data.get("fencing_token", "")),
            epoch=int(data.get("epoch", 0)),
            generation=int(data.get("generation", 1)),
            acquired_at_epoch=acquired_at,
            last_heartbeat_epoch=float(data.get("last_heartbeat_epoch", 0.0)),
            expires_at_epoch=expires_at,
            ttl_s=ttl_s,
            max_expires_at=max_expires_at,
            last_committed_digest=str(data.get("last_committed_digest", "")),
            shared_holders=list(data.get("shared_holders", [])),
        )


@dataclass(frozen=True)
class CommitJournalRecord:
    """Durable Write-Ahead Commit Journal entry in `workspace_lease.journal.json`."""

    tx_id: str = ""
    state: str = "PUBLISH_INTENT"
    scope: str = "profile:*"
    fencing_token: str = ""
    target_path: str = ""
    backup_path: str = ""
    staged_path: str = ""
    staged_digest: str = ""
    started_at_epoch: float = 0.0
    kind: str = "filesystem"
    ref_name: str = ""
    reset_mode: str = "hard"
    refresh_index: bool = False
    caller_backup_path: str = ""
    prune_empty_parents_up_to: str = ""
    stash_sha: str = ""
    stash_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CommitJournalRecord:
        return cls(
            tx_id=str(data.get("tx_id", "")),
            state=str(data.get("state", "PUBLISH_INTENT")),
            scope=str(data.get("scope", "profile:*")),
            fencing_token=str(data.get("fencing_token", "")),
            target_path=str(data.get("target_path", "")),
            backup_path=str(data.get("backup_path", "")),
            staged_path=str(data.get("staged_path", "")),
            staged_digest=str(data.get("staged_digest", "")),
            started_at_epoch=float(data.get("started_at_epoch", 0.0)),
            kind=str(data.get("kind", "filesystem")),
            ref_name=str(data.get("ref_name", "")),
            reset_mode=str(data.get("reset_mode", "hard") or "hard"),
            refresh_index=bool(data.get("refresh_index", False)),
            caller_backup_path=str(data.get("caller_backup_path", "")),
            prune_empty_parents_up_to=str(data.get("prune_empty_parents_up_to", "")),
            stash_sha=str(data.get("stash_sha", "")),
            stash_message=str(data.get("stash_message", "")),
        )


@dataclass
class LeaseExecutionReport:
    """Structured outcome returned by lease operations and persisted to cron ledgers."""

    status: str = "ACQUIRED"
    reason_code: str = "OK"
    exit_code: int = EXIT_OK
    scopes: tuple[str, ...] = ()
    mode: str = "exclusive_cutover"
    holder: str = "cli"
    holder_id: str = ""
    holder_pid: int = 0
    process_start_time_ns: int = 0
    fencing_tokens: dict[str, str] = field(default_factory=dict)
    fencing_token: str = ""
    timestamp_utc: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "exit_code": self.exit_code,
            "scopes": list(self.scopes),
            "mode": self.mode,
            "holder": self.holder,
            "holder_id": self.holder_id,
            "holder_pid": self.holder_pid,
            "process_start_time_ns": self.process_start_time_ns,
            "fencing_tokens": dict(self.fencing_tokens),
            "fencing_token": self.fencing_token,
            "timestamp_utc": self.timestamp_utc
            or datetime.now(UTC).isoformat(),
            "details": dict(self.details),
        }


@dataclass
class _LeaseLedger:
    epoch: int
    scope_generations: dict[str, int] = field(default_factory=dict)
    scope_states: dict[str, str] = field(default_factory=dict)
    active_records: dict[str, LeaseRecord] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "hermes-workspace-leases/v1",
            "epoch": self.epoch,
            "scope_generations": dict(self.scope_generations),
            "scope_states": dict(self.scope_states),
            "active_records": {
                k: v.to_dict() for k, v in self.active_records.items()
            },
            "history": list(self.history[-100:]),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], default_epoch: int) -> _LeaseLedger:
        epoch = int(data.get("epoch", default_epoch))
        raw_gens = data.get("scope_generations")
        if raw_gens is not None and not isinstance(raw_gens, Mapping):
            raise ValueError("scope_generations must be a mapping")
        gens = {str(k): int(v) for k, v in (raw_gens or {}).items()}

        raw_states = data.get("scope_states")
        if raw_states is not None and not isinstance(raw_states, Mapping):
            raise ValueError("scope_states must be a mapping")
        states = {str(k): str(v) for k, v in (raw_states or {}).items()}

        raw_active = data.get("active_records")
        if raw_active is not None and not isinstance(raw_active, Mapping):
            raise ValueError("active_records must be a mapping")
        active: dict[str, LeaseRecord] = {}
        for k, v in (raw_active or {}).items():
            if not isinstance(v, Mapping):
                raise ValueError(f"active_records[{k!r}] must be a mapping")
            active[str(k)] = LeaseRecord.from_dict(v)

        raw_history = data.get("history")
        if raw_history is not None and not isinstance(raw_history, list):
            raise ValueError("history must be a list")
        history = list(raw_history or [])
        return cls(
            epoch=epoch,
            scope_generations=gens,
            scope_states=states,
            active_records=active,
            history=history,
        )
