"""Fenced Workspace Concurrency Lease Coordinator (Module A).

Coordinates cross-process concurrency leases, monotonic fencing tokens,
2-phase transactional staging (`<target>.tmp.<fencing_token>`), a durable
Write-Ahead Commit Journal (`workspace_lease.journal.json`), crash recovery
(`recover_interrupted_commit`), and lock-held atomic commits (`atomic_fenced_commit`)
across repositories, agent profiles, and skill catalogs.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterator, Mapping, Sequence

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DEFERRED = 75
EXIT_PREEMPTED = 78

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

_PROCESS_LOCK_REGISTRY_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


def _get_inproc_lock(lock_path: Path) -> threading.RLock:
    key = os.path.normcase(str(lock_path.resolve()))
    with _PROCESS_LOCK_REGISTRY_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


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
        .replace(":", "_")
        .replace("*", "star")
        .replace("/", "_")
        .replace("\\", "_")
    )


token_to_slug = _token_to_slug


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
    raw = str(fencing_token).strip()
    parts = raw.rsplit(":", 2)
    if len(parts) < 3:
        raise ValueError(f"Cannot infer scope from fencing token {fencing_token!r}")
    return normalize_scope(parts[0])


class _LeaseTokenStr(str):
    """String fencing token carrying optional holder identity for shared-session release."""

    _holder_pid: int | None = None
    _holder: str | None = None


class _LeaseTokenMap(dict[str, str]):
    """Fencing token mapping carrying optional holder identity for shared-session release."""

    _holder_pid: int | None = None
    _holder: str | None = None


def _make_token_str(
    token: str, *, holder_pid: int | None = None, holder: str | None = None
) -> _LeaseTokenStr:
    wrapped = _LeaseTokenStr(token)
    wrapped._holder_pid = holder_pid
    wrapped._holder = holder
    return wrapped


def _make_token_map(
    tokens: Mapping[str, str],
    *,
    holder_pid: int | None = None,
    holder: str | None = None,
) -> _LeaseTokenMap:
    wrapped = _LeaseTokenMap(
        {
            k: _make_token_str(v, holder_pid=holder_pid, holder=holder)
            for k, v in tokens.items()
        }
    )
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
        gens = {
            str(k): int(v)
            for k, v in (data.get("scope_generations") or {}).items()
        }
        states = {
            str(k): str(v) for k, v in (data.get("scope_states") or {}).items()
        }
        active = {
            str(k): LeaseRecord.from_dict(v)
            for k, v in (data.get("active_records") or {}).items()
            if isinstance(v, Mapping)
        }
        history = list(data.get("history") or [])
        return cls(
            epoch=epoch,
            scope_generations=gens,
            scope_states=states,
            active_records=active,
            history=history,
        )


def _default_process_start_time_ns(pid: int) -> int | None:
    """Query OS process creation timestamp in nanoseconds for `(pid, start_time_ns)` fencing."""
    if pid <= 0:
        return None
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return None
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                if exit_code.value != STILL_ACTIVE:
                    return None
            creation = ctypes.c_ulonglong()
            exit_t = ctypes.c_ulonglong()
            kernel_t = ctypes.c_ulonglong()
            user_t = ctypes.c_ulonglong()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            )
            if not ok:
                return None
            return int(creation.value) * 100
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            try:
                content = stat_path.read_text(encoding="utf-8")
                after_comm = content.rsplit(")", 1)[-1].split()
                return int(after_comm[19])
            except Exception:
                return 1
        return 1


def _default_lease_dir() -> Path:
    env_dir = os.environ.get("HERMES_LEASE_DIR")
    if env_dir:
        return Path(env_dir)
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home) / "leases"
    return Path.home() / ".hermes" / "leases"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


class WorkspaceLeaseCoordinator:
    """Deep coordinator module owning cross-process lease state, commit journal, and fenced commits."""

    def __init__(
        self,
        lease_dir: Path | str | None = None,
        state_db_path: Path | str | None = None,
        worktrees_root: Path | str | None = None,
        process_inspector: Callable[[int], int | None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.lease_dir = Path(lease_dir) if lease_dir is not None else _default_lease_dir()
        self.lock_path = self.lease_dir / "workspace_lease.lock"
        self.ledger_path = self.lease_dir / "workspace_leases.json"
        self.journal_path = self.lease_dir / "workspace_lease.journal.json"
        self.state_db_path = Path(state_db_path) if state_db_path is not None else None
        self.worktrees_root = Path(worktrees_root) if worktrees_root is not None else None
        self._process_inspector = process_inspector or _default_process_start_time_ns
        self._clock = clock or time.time

    def _now(self) -> float:
        return float(self._clock())

    @contextlib.contextmanager
    def _acquire_kernel_mutex(self, timeout_s: float = 5.0) -> Iterator[None]:
        """Acquire both in-process RLock and OS kernel byte-range lock on `workspace_lease.lock`."""
        self.lease_dir.mkdir(parents=True, exist_ok=True)
        inproc_lock = _get_inproc_lock(self.lock_path)
        deadline = time.monotonic() + max(0.05, float(timeout_s))
        acquired_inproc = inproc_lock.acquire(timeout=max(0.05, float(timeout_s)))
        if not acquired_inproc:
            raise TimeoutError(f"Timed out acquiring in-process mutex for {self.lock_path}")
        fd: int | None = None
        locked_os = False
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o666)
            if os.path.getsize(self.lock_path) == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked_os = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring OS kernel lock on {self.lock_path}"
                        ) from exc
                    time.sleep(0.02)
            yield
        finally:
            if fd is not None:
                if locked_os:
                    with contextlib.suppress(OSError):
                        os.lseek(fd, 0, os.SEEK_SET)
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(fd, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    os.close(fd)
            inproc_lock.release()

    def _read_ledger_locked(self) -> _LeaseLedger:
        default_epoch = int(self._now())
        if not self.ledger_path.is_file():
            return _LeaseLedger(epoch=default_epoch)
        try:
            raw = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            if isinstance(raw, Mapping):
                return _LeaseLedger.from_dict(raw, default_epoch=default_epoch)
        except Exception:
            pass
        return _LeaseLedger(epoch=default_epoch)

    def _write_ledger_locked(self, ledger: _LeaseLedger) -> None:
        _atomic_write_json(self.ledger_path, ledger.to_dict())

    def _read_journal_locked(self) -> tuple[CommitJournalRecord | None, list[dict[str, Any]]]:
        if not self.journal_path.is_file():
            return None, []
        try:
            raw = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except Exception:
            return None, []
        if not isinstance(raw, Mapping):
            return None, []
        history = list(raw.get("history") or [])
        active_raw = raw.get("active_tx")
        if isinstance(active_raw, Mapping) and active_raw.get("tx_id"):
            return CommitJournalRecord.from_dict(active_raw), history
        if raw.get("tx_id") and raw.get("state") in ("PUBLISH_INTENT", "PUBLISHED_LEDGER_PENDING"):
            return CommitJournalRecord.from_dict(raw), history
        return None, history

    def _write_journal_locked(
        self,
        entry: CommitJournalRecord | None,
        history: list[dict[str, Any]] | None = None,
    ) -> None:
        _, existing_history = self._read_journal_locked()
        merged_history = existing_history if history is None else history
        payload: dict[str, Any] = {
            "schema": "hermes-workspace-lease-journal/v1",
            "active_tx": entry.to_dict() if entry is not None else None,
            "history": merged_history[-100:],
        }
        _atomic_write_json(self.journal_path, payload)

    def _clear_journal_locked(
        self,
        tx_id: str,
        final_state: str = "COMMITTED",
        entry: CommitJournalRecord | None = None,
    ) -> CommitJournalRecord | None:
        active, history = self._read_journal_locked()
        target_entry = entry or active
        completed: CommitJournalRecord | None = None
        if target_entry is not None and (not tx_id or target_entry.tx_id == tx_id):
            completed = replace(target_entry, state=final_state)
            history.append(completed.to_dict())
        self._write_journal_locked(None, history=history)
        return completed

    def _compute_path_digest(self, path: Path | str) -> str:
        """Compute deterministic SHA-256 over a single file or directory tree."""
        p = Path(path)
        if not p.exists():
            return ""
        hasher = hashlib.sha256()
        if p.is_file():
            hasher.update(b"F:")
            hasher.update(p.read_bytes())
            return hasher.hexdigest()
        hasher.update(b"D:")
        for child in sorted(p.rglob("*"), key=lambda item: item.relative_to(p).as_posix()):
            rel = child.relative_to(p).as_posix()
            if child.is_dir():
                hasher.update(f"D:{rel}\n".encode())
            elif child.is_file():
                hasher.update(f"F:{rel}:".encode())
                hasher.update(hashlib.sha256(child.read_bytes()).hexdigest().encode("ascii"))
                hasher.update(b"\n")
        return hasher.hexdigest()

    def _purge_staged_target_nolock(self, staged_path: Path | str) -> None:
        p = Path(staged_path)
        if not p.exists():
            return
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                p.unlink(missing_ok=True)

    def _cleanup_backup_nolock(self, backup_path: Path | str) -> None:
        self._purge_staged_target_nolock(backup_path)

    def _prune_empty_parents_nolock(
        self, start_dir: Path | str, stop_at_dir: Path | str
    ) -> None:
        cur = Path(start_dir).resolve()
        boundary = Path(stop_at_dir).resolve()
        while (
            cur != boundary
            and str(cur).startswith(str(boundary) + os.sep)
            and cur.is_dir()
        ):
            try:
                if any(cur.iterdir()):
                    break
                cur.rmdir()
                cur = cur.parent
            except OSError:
                break

    def _atomic_publish_locked(
        self,
        staged_path: Path,
        target_path: Path,
        backup_path: Path,
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if staged_path.is_dir() and not staged_path.is_symlink():
            if backup_path.exists():
                self._cleanup_backup_nolock(backup_path)
            if target_path.exists():
                os.replace(target_path, backup_path)
            os.replace(staged_path, target_path)
        else:
            if target_path.exists() and target_path.is_file():
                shutil.copy2(target_path, backup_path)
            os.replace(staged_path, target_path)

    def _recover_interrupted_commit_locked(
        self, ledger: _LeaseLedger
    ) -> list[CommitJournalRecord]:
        active_tx, _ = self._read_journal_locked()
        if active_tx is None or active_tx.state not in (
            "PUBLISH_INTENT",
            "PUBLISHED_LEDGER_PENDING",
        ):
            return []

        now = self._now()
        if active_tx.kind == "git_ref":
            repo_root = Path(active_tx.target_path)
            current_sha = ""
            with contextlib.suppress(Exception):
                current_sha = (
                    subprocess.check_output(
                        ["git", "-C", str(repo_root), "rev-parse", active_tx.ref_name or "HEAD"],
                        text=True,
                        stderr=subprocess.DEVNULL,
                    ).strip()
                )
            if current_sha == active_tx.staged_digest:
                reset_mode = getattr(active_tx, "reset_mode", "hard") or "hard"
                if reset_mode == "mixed":
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(repo_root),
                            "reset",
                            "--quiet",
                            "--mixed",
                            active_tx.staged_digest,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                else:
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(repo_root),
                            "reset",
                            "--hard",
                            active_tx.staged_digest,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                if getattr(active_tx, "refresh_index", False):
                    diff_out = subprocess.check_output(
                        ["git", "-C", str(repo_root), "diff", "--name-only", "HEAD", "--", "."],
                        text=True,
                        stderr=subprocess.DEVNULL,
                    ).strip()
                    if not diff_out:
                        subprocess.run(
                            [
                                "git",
                                "-c",
                                "core.safecrlf=false",
                                "-C",
                                str(repo_root),
                                "add",
                                "-u",
                                "--",
                                ".",
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                record = ledger.active_records.get(active_tx.scope)
                if record is not None:
                    record.last_committed_digest = active_tx.staged_digest
                    record.last_heartbeat_epoch = now
                    hard_ceiling = record.max_expires_at or (
                        record.acquired_at_epoch + min(record.ttl_s, MAX_LEASE_TTL_S)
                    )
                    record.expires_at_epoch = min(
                        now + DEFAULT_HEARTBEAT_WINDOW_S,
                        hard_ceiling,
                    )
                self._write_ledger_locked(ledger)
                done = self._clear_journal_locked(
                    active_tx.tx_id, final_state="COMMITTED", entry=active_tx
                )
                return [done] if done else []
            done = self._clear_journal_locked(
                active_tx.tx_id, final_state="ABORTED", entry=active_tx
            )
            return [done] if done else []

        if active_tx.kind == "delete":
            target_path = Path(active_tx.target_path)
            backup_path = Path(active_tx.backup_path) if active_tx.backup_path else None
            caller_bk = (
                Path(active_tx.caller_backup_path)
                if getattr(active_tx, "caller_backup_path", "")
                else None
            )
            prune_root = (
                Path(active_tx.prune_empty_parents_up_to)
                if getattr(active_tx, "prune_empty_parents_up_to", "")
                else None
            )
            if active_tx.state == "PUBLISHED_LEDGER_PENDING" or not target_path.exists():
                if (
                    caller_bk is not None
                    and not caller_bk.exists()
                    and backup_path is not None
                    and backup_path.exists()
                ):
                    caller_bk.parent.mkdir(parents=True, exist_ok=True)
                    if backup_path.is_dir():
                        shutil.copytree(backup_path, caller_bk, dirs_exist_ok=True)
                    else:
                        shutil.copy2(backup_path, caller_bk)
                if target_path.exists():
                    self._purge_staged_target_nolock(target_path)
                if prune_root is not None:
                    self._prune_empty_parents_nolock(target_path.parent, prune_root)
                record = ledger.active_records.get(active_tx.scope)
                if record is not None:
                    record.last_committed_digest = active_tx.staged_digest
                    record.last_heartbeat_epoch = now
                    hard_ceiling = record.max_expires_at or (
                        record.acquired_at_epoch + min(record.ttl_s, MAX_LEASE_TTL_S)
                    )
                    record.expires_at_epoch = min(
                        now + DEFAULT_HEARTBEAT_WINDOW_S,
                        hard_ceiling,
                    )
                self._write_ledger_locked(ledger)
                if backup_path is not None and backup_path.exists():
                    self._cleanup_backup_nolock(backup_path)
                done = self._clear_journal_locked(
                    active_tx.tx_id, final_state="COMMITTED", entry=active_tx
                )
                return [done] if done else []
            if backup_path is not None and backup_path.exists():
                self._cleanup_backup_nolock(backup_path)
            done = self._clear_journal_locked(
                active_tx.tx_id, final_state="ABORTED", entry=active_tx
            )
            return [done] if done else []

        target_path = Path(active_tx.target_path)
        backup_path = Path(active_tx.backup_path) if active_tx.backup_path else None
        staged_path = Path(active_tx.staged_path) if active_tx.staged_path else None

        target_exists = target_path.exists()
        target_digest = (
            self._compute_path_digest(target_path) if target_exists else ""
        )

        # Case 1: Post-Publication / Pre-Ledger Split-Brain
        if target_exists and active_tx.staged_digest and target_digest == active_tx.staged_digest:
            record = ledger.active_records.get(active_tx.scope)
            if record is not None:
                record.last_committed_digest = active_tx.staged_digest
                record.last_heartbeat_epoch = now
                hard_ceiling = record.max_expires_at or (
                    record.acquired_at_epoch + min(record.ttl_s, MAX_LEASE_TTL_S)
                )
                record.expires_at_epoch = min(
                    now + DEFAULT_HEARTBEAT_WINDOW_S,
                    hard_ceiling,
                )
            self._write_ledger_locked(ledger)
            if backup_path is not None and backup_path.exists():
                self._cleanup_backup_nolock(backup_path)
            if staged_path is not None and staged_path.exists():
                self._purge_staged_target_nolock(staged_path)
            done = self._clear_journal_locked(
                active_tx.tx_id, final_state="COMMITTED", entry=active_tx
            )
            return [done] if done else []

        # Case 2: Mid-Directory-Swap Crash (target missing or mismatched after target -> backup)
        if backup_path is not None and backup_path.exists():
            if target_exists:
                self._purge_staged_target_nolock(target_path)
            os.replace(backup_path, target_path)
            if staged_path is not None and staged_path.exists():
                self._purge_staged_target_nolock(staged_path)
            done = self._clear_journal_locked(
                active_tx.tx_id, final_state="ROLLED_BACK", entry=active_tx
            )
            return [done] if done else []

        # Case 3: Pre-Swap Crash (target untouched, staged temp still present)
        if staged_path is not None and staged_path.exists():
            self._purge_staged_target_nolock(staged_path)
        done = self._clear_journal_locked(
            active_tx.tx_id, final_state="ABORTED", entry=active_tx
        )
        return [done] if done else []

    def recover_interrupted_commit(self) -> list[CommitJournalRecord]:
        """Reconcile any uncommitted Write-Ahead Commit Journal entry under `workspace_lease.lock`."""
        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            return self._recover_interrupted_commit_locked(ledger)

    def _is_holder_alive(self, pid: int, recorded_start_ns: int) -> bool:
        if pid <= 0:
            return True
        live_start_ns = self._process_inspector(int(pid))
        if live_start_ns is None:
            return False
        if recorded_start_ns > 0 and live_start_ns > 0 and live_start_ns != recorded_start_ns:
            return False
        return True

    def _reclaim_stale_records_locked(
        self, ledger: _LeaseLedger, target_scopes: Sequence[str], now: float
    ) -> None:
        to_preempt: list[str] = []
        for active_scope, rec in list(ledger.active_records.items()):
            if not any(scopes_overlap(active_scope, req) for req in target_scopes):
                continue
            hard_ceiling = min(
                rec.acquired_at_epoch + min(rec.ttl_s, MAX_LEASE_TTL_S),
                rec.max_expires_at or (rec.acquired_at_epoch + MAX_LEASE_TTL_S),
            )
            expired = (
                now > rec.expires_at_epoch
                or now > hard_ceiling
            )
            alive = self._is_holder_alive(rec.holder_pid, rec.process_start_time_ns)
            if rec.shared_holders and rec.mode == "shared_session" and not expired:
                still_live_holders = [
                    sh
                    for sh in rec.shared_holders
                    if self._is_holder_alive(
                        int(sh.get("pid", sh.get("holder_pid", 0))),
                        int(sh.get("process_start_time_ns", 0)),
                    )
                ]
                rec.shared_holders = still_live_holders
                if still_live_holders:
                    rec.holder = str(still_live_holders[0].get("holder", rec.holder))
                    rec.holder_pid = int(
                        still_live_holders[0].get(
                            "pid", still_live_holders[0].get("holder_pid", 0)
                        )
                    )
                    rec.process_start_time_ns = int(
                        still_live_holders[0].get("process_start_time_ns", 0)
                    )
                    alive = True
                else:
                    alive = False
            if expired or not alive:
                to_preempt.append(active_scope)

        for scope_key in to_preempt:
            stale_rec = ledger.active_records.pop(scope_key)
            stale_rec.state = "EXPIRED"
            ledger.history.append(replace(stale_rec, state="EXPIRED").to_dict())
            stale_rec.state = "PREEMPTED"
            ledger.history.append(stale_rec.to_dict())
            ledger.scope_states[scope_key] = "PREEMPTED"

    def _check_external_contention_locked(
        self, now: float
    ) -> tuple[str, dict[str, Any]] | None:
        if self.state_db_path is not None and self.state_db_path.is_file():
            try:
                uri = f"file:{self.state_db_path.as_posix()}?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=1.0)
                try:
                    cutoff = now - ACTIVE_SESSION_WINDOW_S
                    cur = conn.execute(
                        "SELECT id, status, updated_at FROM sessions "
                        "WHERE lower(status) IN ('running', 'active', 'in_progress') "
                        "AND updated_at >= ? LIMIT 1",
                        (cutoff,),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        return (
                            "DEFERRED_ACTIVE_SESSION",
                            {
                                "session_id": row[0],
                                "status": row[1],
                                "updated_at": row[2],
                            },
                        )
                finally:
                    conn.close()
            except Exception:
                pass

        if self.worktrees_root is not None and self.worktrees_root.is_dir():
            for wt_dir in sorted(self.worktrees_root.glob("wt-*")):
                if not wt_dir.is_dir():
                    continue
                try:
                    out = subprocess.check_output(
                        ["git", "-C", str(wt_dir), "status", "--porcelain"],
                        text=True,
                        stderr=subprocess.DEVNULL,
                        timeout=3.0,
                    ).strip()
                    if out:
                        return (
                            "DEFERRED_ACTIVE_WORKTREE",
                            {"worktree": str(wt_dir)},
                        )
                except Exception:
                    continue
        return None

    def _try_acquire_once(
        self,
        canonical_scopes: tuple[str, ...],
        mode: str,
        holder: str,
        ttl_s: float,
        holder_pid: int,
        process_start_time_ns: int,
    ) -> LeaseExecutionReport:
        clamped_ttl = _clamp_ttl_s(ttl_s)
        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            self._recover_interrupted_commit_locked(ledger)
            now = self._now()

            for sc in canonical_scopes:
                ledger.scope_states[sc] = "ACQUIRING"

            self._reclaim_stale_records_locked(ledger, canonical_scopes, now)

            # Check overlapping active lease records
            for req_scope in canonical_scopes:
                for active_scope, active_rec in ledger.active_records.items():
                    if not scopes_overlap(req_scope, active_scope):
                        continue
                    if mode == "shared_session" and active_rec.mode == "shared_session":
                        continue
                    for sc in canonical_scopes:
                        if sc not in ledger.active_records:
                            ledger.scope_states[sc] = "UNLOCKED"
                    self._write_ledger_locked(ledger)
                    return LeaseExecutionReport(
                        status="DEFERRED",
                        reason_code="DEFERRED_LEASE_HELD",
                        exit_code=EXIT_DEFERRED,
                        scopes=canonical_scopes,
                        mode=mode,
                        holder=holder,
                        details={
                            "conflicting_scope": active_scope,
                            "conflicting_holder": active_rec.holder,
                            "conflicting_mode": active_rec.mode,
                            "conflicting_fencing_token": active_rec.fencing_token,
                        },
                    )

            if mode == "exclusive_cutover":
                ext_conflict = self._check_external_contention_locked(now)
                if ext_conflict is not None:
                    reason_code, details = ext_conflict
                    for sc in canonical_scopes:
                        if sc not in ledger.active_records:
                            ledger.scope_states[sc] = "UNLOCKED"
                    self._write_ledger_locked(ledger)
                    return LeaseExecutionReport(
                        status="DEFERRED",
                        reason_code=reason_code,
                        exit_code=EXIT_DEFERRED,
                        scopes=canonical_scopes,
                        mode=mode,
                        holder=holder,
                        details=details,
                    )

            fencing_tokens: dict[str, str] = {}
            max_expires_at = min(now + clamped_ttl, now + MAX_LEASE_TTL_S)
            expires_at = min(now + DEFAULT_HEARTBEAT_WINDOW_S, max_expires_at)
            for sc in canonical_scopes:
                existing = ledger.active_records.get(sc)
                if (
                    existing is not None
                    and mode == "shared_session"
                    and existing.mode == "shared_session"
                ):
                    existing_ceiling = min(
                        existing.acquired_at_epoch + min(existing.ttl_s, MAX_LEASE_TTL_S),
                        existing.max_expires_at or (existing.acquired_at_epoch + MAX_LEASE_TTL_S),
                    )
                    existing.max_expires_at = existing_ceiling
                    existing.last_heartbeat_epoch = now
                    existing.expires_at_epoch = min(
                        max(existing.expires_at_epoch, expires_at),
                        existing_ceiling,
                    )
                    existing.shared_holders = [
                        sh
                        for sh in existing.shared_holders
                        if not (
                            int(sh.get("pid", sh.get("holder_pid", 0))) == holder_pid
                            and int(sh.get("process_start_time_ns", 0)) == process_start_time_ns
                            and str(sh.get("holder", "")) == holder
                        )
                    ]
                    existing.shared_holders.append(
                        {
                            "holder": holder,
                            "pid": holder_pid,
                            "holder_pid": holder_pid,
                            "process_start_time_ns": process_start_time_ns,
                            "acquired_at_epoch": now,
                        }
                    )
                    fencing_tokens[sc] = _make_token_str(
                        existing.fencing_token, holder_pid=holder_pid, holder=holder
                    )
                    ledger.scope_states[sc] = "ACQUIRED"
                    continue

                gen = int(ledger.scope_generations.get(sc, 0)) + 1
                ledger.scope_generations[sc] = gen
                token = f"{sc}:{ledger.epoch}:{gen}"
                rec = LeaseRecord(
                    scope=sc,
                    state="ACQUIRED",
                    mode=mode,
                    holder=holder,
                    holder_pid=holder_pid,
                    process_start_time_ns=process_start_time_ns,
                    fencing_token=token,
                    epoch=ledger.epoch,
                    generation=gen,
                    acquired_at_epoch=now,
                    last_heartbeat_epoch=now,
                    expires_at_epoch=expires_at,
                    ttl_s=clamped_ttl,
                    max_expires_at=max_expires_at,
                    shared_holders=(
                        [
                            {
                                "holder": holder,
                                "pid": holder_pid,
                                "holder_pid": holder_pid,
                                "process_start_time_ns": process_start_time_ns,
                                "acquired_at_epoch": now,
                            }
                        ]
                        if mode == "shared_session"
                        else []
                    ),
                )
                ledger.active_records[sc] = rec
                ledger.scope_states[sc] = "ACQUIRED"
                fencing_tokens[sc] = _make_token_str(
                    token, holder_pid=holder_pid, holder=holder
                )

            self._write_ledger_locked(ledger)
            wrapped_tokens = _make_token_map(
                fencing_tokens, holder_pid=holder_pid, holder=holder
            )
            primary_token = (
                wrapped_tokens[canonical_scopes[0]]
                if canonical_scopes
                else _make_token_str("", holder_pid=holder_pid, holder=holder)
            )
            return LeaseExecutionReport(
                status="ACQUIRED",
                reason_code="LEASE_ACQUIRED",
                exit_code=EXIT_OK,
                scopes=canonical_scopes,
                mode=mode,
                holder=holder,
                fencing_tokens=wrapped_tokens,
                fencing_token=primary_token,
            )

    def acquire_workspace_lease(
        self,
        scopes: Sequence[str] | str,
        mode: str = "exclusive_cutover",
        holder: str = "cli",
        ttl_s: float = MAX_LEASE_TTL_S,
        wait_seconds: float = 0.0,
        poll_interval_s: float = 2.0,
        holder_pid: int | None = None,
        process_start_time_ns: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> LeaseExecutionReport:
        """Acquire a fenced lease over `scopes` in `shared_session` or `exclusive_cutover` mode."""
        if isinstance(scopes, str):
            raw_list = [s for s in scopes.split(",") if s.strip()]
        else:
            raw_list = list(scopes)
        if not raw_list:
            raise ValueError("At least one lease scope is required")
        canonical_scopes = tuple(dict.fromkeys(normalize_scope(s) for s in raw_list))
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid lease mode {mode!r}: expected one of {sorted(VALID_MODES)}")

        clamped_ttl = _clamp_ttl_s(ttl_s)
        pid = int(os.getpid() if holder_pid is None else holder_pid)
        start_ns = (
            int(process_start_time_ns)
            if process_start_time_ns is not None
            else int(self._process_inspector(pid) or 0)
        )

        report = self._try_acquire_once(
            canonical_scopes=canonical_scopes,
            mode=mode,
            holder=holder,
            ttl_s=clamped_ttl,
            holder_pid=pid,
            process_start_time_ns=start_ns,
        )
        if report.status == "ACQUIRED" or float(wait_seconds) <= 0.0:
            return report

        local_stop = stop_event or threading.Event()
        prev_sigint = None
        installed_sigint = False

        def _on_sigint(signum: int, frame: Any) -> None:
            local_stop.set()

        if threading.current_thread() is threading.main_thread():
            with contextlib.suppress(ValueError, OSError):
                prev_sigint = signal.getsignal(signal.SIGINT)
                signal.signal(signal.SIGINT, _on_sigint)
                installed_sigint = True

        deadline = time.monotonic() + float(wait_seconds)
        step = max(0.02, min(float(poll_interval_s), 2.0))
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                wait_slice = min(step, max(0.0, remaining))
                if local_stop.wait(timeout=wait_slice):
                    return LeaseExecutionReport(
                        status="DEFERRED",
                        reason_code="INTERRUPTED_SIGINT",
                        exit_code=EXIT_DEFERRED,
                        scopes=canonical_scopes,
                        mode=mode,
                        holder=holder,
                    )
                if local_stop.is_set():
                    return LeaseExecutionReport(
                        status="DEFERRED",
                        reason_code="INTERRUPTED_SIGINT",
                        exit_code=EXIT_DEFERRED,
                        scopes=canonical_scopes,
                        mode=mode,
                        holder=holder,
                    )
                report = self._try_acquire_once(
                    canonical_scopes=canonical_scopes,
                    mode=mode,
                    holder=holder,
                    ttl_s=clamped_ttl,
                    holder_pid=pid,
                    process_start_time_ns=start_ns,
                )
                if report.status == "ACQUIRED":
                    return report
            return report
        finally:
            if installed_sigint and prev_sigint is not None:
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(signal.SIGINT, prev_sigint)

    def renew_heartbeat(
        self,
        scope: str,
        acquired_token: str,
        now_epoch: float | None = None,
    ) -> LeaseRecord:
        """Renew the 30s heartbeat rolling window (90s window capped at `max_expires_at`)."""
        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            canonical = normalize_scope(scope)
            record = ledger.active_records.get(canonical)
            now = float(self._now() if now_epoch is None else now_epoch)
            if record is not None:
                clamped_ttl = _clamp_ttl_s(record.ttl_s)
                hard_ceiling = min(
                    record.acquired_at_epoch + clamped_ttl,
                    record.acquired_at_epoch + MAX_LEASE_TTL_S,
                    record.max_expires_at or (record.acquired_at_epoch + MAX_LEASE_TTL_S),
                )
                record.ttl_s = clamped_ttl
                record.max_expires_at = hard_ceiling
            else:
                hard_ceiling = 0.0
            if (
                record is None
                or record.state != "ACQUIRED"
                or record.fencing_token != acquired_token
                or now > record.expires_at_epoch
                or now > hard_ceiling
            ):
                raise LeasePreemptedError(
                    f"Cannot renew heartbeat: fencing token {acquired_token!r} is expired or preempted.",
                    fencing_token=acquired_token,
                )
            record.last_heartbeat_epoch = now
            record.expires_at_epoch = min(
                now + DEFAULT_HEARTBEAT_WINDOW_S,
                now + record.ttl_s,
                record.max_expires_at,
            )
            self._write_ledger_locked(ledger)
            return replace(record)

    renew_lease_heartbeat = renew_heartbeat

    def atomic_fenced_commit(
        self,
        scope: str,
        acquired_token: str,
        staged_path: Path | str,
        target_path: Path | str,
    ) -> str:
        """Execute Phase B/C journaled lock-held atomic commit for a file or directory."""
        staged = Path(staged_path)
        target = Path(target_path)
        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            self._recover_interrupted_commit_locked(ledger)
            canonical = normalize_scope(scope)
            record = ledger.active_records.get(canonical)
            now = self._now()
            hard_ceiling = (
                min(
                    record.acquired_at_epoch + min(record.ttl_s, MAX_LEASE_TTL_S),
                    record.max_expires_at or (record.acquired_at_epoch + MAX_LEASE_TTL_S),
                )
                if record is not None
                else 0.0
            )
            if (
                record is None
                or record.state != "ACQUIRED"
                or record.fencing_token != acquired_token
                or now > record.expires_at_epoch
                or now > hard_ceiling
            ):
                self._purge_staged_target_nolock(staged)
                raise LeasePreemptedError(
                    f"Fencing token {acquired_token!r} preempted or expired; "
                    f"purged {staged} with 0 target side effects.",
                    fencing_token=acquired_token,
                    staged_path=str(staged),
                )
            if not staged.exists():
                raise FileNotFoundError(f"Staged path does not exist: {staged}")

            token_slug = _token_to_slug(acquired_token)
            backup_path = target.with_name(f"{target.name}.prev.{token_slug}")
            staged_digest = self._compute_path_digest(staged)
            journal_entry = CommitJournalRecord(
                tx_id=f"tx_{int(now * 1000)}_{token_slug}",
                state="PUBLISH_INTENT",
                scope=canonical,
                fencing_token=acquired_token,
                target_path=str(target),
                backup_path=str(backup_path),
                staged_path=str(staged),
                staged_digest=staged_digest,
                started_at_epoch=now,
                kind="filesystem",
            )
            self._write_journal_locked(journal_entry)
            self._atomic_publish_locked(staged, target, backup_path)
            journal_entry = replace(journal_entry, state="PUBLISHED_LEDGER_PENDING")
            self._write_journal_locked(journal_entry)
            record.last_heartbeat_epoch = now
            record.expires_at_epoch = min(
                now + DEFAULT_HEARTBEAT_WINDOW_S,
                hard_ceiling,
            )
            record.last_committed_digest = staged_digest
            self._write_ledger_locked(ledger)
            self._cleanup_backup_nolock(backup_path)
            self._clear_journal_locked(
                journal_entry.tx_id, final_state="COMMITTED", entry=journal_entry
            )
            return staged_digest

    def atomic_fenced_delete(
        self,
        target_path: Path | str,
        fencing_token: str = "",
        maybe_target_path: Path | str | None = None,
        *,
        scope: str | None = None,
        acquired_token: str | None = None,
        backup_path: Path | str | None = None,
        prune_empty_parents_up_to: Path | str | None = None,
    ) -> str:
        """Execute a coordinator-owned, token-checked, journaled deletion/quarantine move."""
        if maybe_target_path is not None:
            resolved_scope = normalize_scope(str(target_path))
            token = str(acquired_token or fencing_token)
            target = Path(maybe_target_path)
        else:
            token = str(acquired_token or fencing_token)
            target = Path(target_path)
            resolved_scope = (
                normalize_scope(scope)
                if scope
                else _extract_scope_from_token(token)
            )

        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            self._recover_interrupted_commit_locked(ledger)
            record = ledger.active_records.get(resolved_scope)
            now = self._now()
            hard_ceiling = (
                min(
                    record.acquired_at_epoch + min(record.ttl_s, MAX_LEASE_TTL_S),
                    record.max_expires_at or (record.acquired_at_epoch + MAX_LEASE_TTL_S),
                )
                if record is not None
                else 0.0
            )
            if (
                record is None
                or record.state != "ACQUIRED"
                or record.fencing_token != token
                or now > record.expires_at_epoch
                or now > hard_ceiling
            ):
                raise LeasePreemptedError(
                    f"Fencing token {token!r} preempted or expired; "
                    f"aborted delete on {target} with 0 target side effects.",
                    fencing_token=token,
                    staged_path=str(target),
                )

            token_slug = _token_to_slug(token)
            self.lease_dir.mkdir(parents=True, exist_ok=True)
            internal_backup = (
                self.lease_dir / f".delete_backup_{int(now * 1000)}_{token_slug}"
            )
            pre_digest = (
                self._compute_path_digest(target) if target.exists() else "MISSING"
            )
            deleted_digest = f"DELETED:{pre_digest}"
            journal_entry = CommitJournalRecord(
                tx_id=f"tx_{int(now * 1000)}_{token_slug}",
                state="PUBLISH_INTENT",
                scope=resolved_scope,
                fencing_token=token,
                target_path=str(target),
                backup_path=str(internal_backup),
                staged_path="",
                staged_digest=deleted_digest,
                started_at_epoch=now,
                kind="delete",
                caller_backup_path=str(backup_path) if backup_path else "",
                prune_empty_parents_up_to=(
                    str(prune_empty_parents_up_to)
                    if prune_empty_parents_up_to
                    else ""
                ),
            )
            self._write_journal_locked(journal_entry)

            if target.exists():
                if internal_backup.exists():
                    self._cleanup_backup_nolock(internal_backup)
                if target.is_dir() and not target.is_symlink():
                    shutil.copytree(target, internal_backup, dirs_exist_ok=True)
                else:
                    shutil.copy2(target, internal_backup)

                if backup_path is not None:
                    bk = Path(backup_path)
                    bk.parent.mkdir(parents=True, exist_ok=True)
                    bk_staged = bk.with_name(f"{bk.name}.tmp.{token_slug}")
                    if bk_staged.exists():
                        self._purge_staged_target_nolock(bk_staged)
                    if target.is_dir() and not target.is_symlink():
                        shutil.copytree(target, bk_staged, dirs_exist_ok=True)
                        if bk.exists():
                            self._purge_staged_target_nolock(bk)
                        os.replace(bk_staged, bk)
                    else:
                        shutil.copy2(target, bk_staged)
                        os.replace(bk_staged, bk)

                self._purge_staged_target_nolock(target)
                if prune_empty_parents_up_to is not None:
                    self._prune_empty_parents_nolock(
                        target.parent, Path(prune_empty_parents_up_to)
                    )

            journal_entry = replace(journal_entry, state="PUBLISHED_LEDGER_PENDING")
            self._write_journal_locked(journal_entry)
            record.last_heartbeat_epoch = now
            record.expires_at_epoch = min(
                now + DEFAULT_HEARTBEAT_WINDOW_S,
                hard_ceiling,
            )
            record.last_committed_digest = deleted_digest
            self._write_ledger_locked(ledger)
            self._cleanup_backup_nolock(internal_backup)
            self._clear_journal_locked(
                journal_entry.tx_id, final_state="COMMITTED", entry=journal_entry
            )
            return deleted_digest

    def atomic_fenced_git_ref_update(
        self,
        scope: str,
        acquired_token: str,
        repo_root: Path | str,
        ref_name: str,
        new_sha: str,
        old_sha: str | None = None,
        *,
        reset_mode: str = "hard",
        fetch_from: str | None = None,
        fetch_refspec: str | None = None,
        refresh_index: bool = False,
    ) -> str:
        """Execute a fenced, journaled Git ref fast-forward + working-tree sync inside `workspace_lease.lock`."""
        repo = Path(repo_root)
        if reset_mode not in ("hard", "mixed"):
            raise ValueError(f"Unsupported git reset_mode {reset_mode!r}: expected 'hard' or 'mixed'")
        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            self._recover_interrupted_commit_locked(ledger)
            canonical = normalize_scope(scope)
            record = ledger.active_records.get(canonical)
            now = self._now()
            hard_ceiling = (
                min(
                    record.acquired_at_epoch + min(record.ttl_s, MAX_LEASE_TTL_S),
                    record.max_expires_at or (record.acquired_at_epoch + MAX_LEASE_TTL_S),
                )
                if record is not None
                else 0.0
            )
            if (
                record is None
                or record.state != "ACQUIRED"
                or record.fencing_token != acquired_token
                or now > record.expires_at_epoch
                or now > hard_ceiling
            ):
                raise LeasePreemptedError(
                    f"Fencing token {acquired_token!r} preempted or expired; "
                    f"aborted git ref update on {repo} with 0 side effects.",
                    fencing_token=acquired_token,
                    staged_path=str(repo),
                )

            if fetch_from:
                fetch_cmd = ["git", "-C", str(repo), "fetch", "--quiet", "--no-tags", str(fetch_from)]
                if fetch_refspec:
                    fetch_cmd.append(str(fetch_refspec))
                subprocess.run(fetch_cmd, check=True, capture_output=True, text=True)

            resolved_old = old_sha
            if not resolved_old:
                resolved_old = (
                    subprocess.check_output(
                        ["git", "-C", str(repo), "rev-parse", ref_name],
                        text=True,
                    ).strip()
                )
            resolved_new = (
                subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", f"{new_sha}^{{commit}}"],
                    text=True,
                ).strip()
            )
            token_slug = _token_to_slug(acquired_token)
            journal_entry = CommitJournalRecord(
                tx_id=f"tx_{int(now * 1000)}_{token_slug}",
                state="PUBLISH_INTENT",
                scope=canonical,
                fencing_token=acquired_token,
                target_path=str(repo),
                backup_path=resolved_old,
                staged_path=resolved_new,
                staged_digest=resolved_new,
                started_at_epoch=now,
                kind="git_ref",
                ref_name=ref_name,
                reset_mode=reset_mode,
                refresh_index=bool(refresh_index),
            )
            self._write_journal_locked(journal_entry)

            if resolved_old != resolved_new:
                if ref_name and ref_name != "HEAD":
                    cmd = ["git", "-C", str(repo), "update-ref", ref_name, resolved_new, resolved_old]
                    subprocess.run(cmd, check=True, capture_output=True, text=True)
                if reset_mode == "mixed":
                    subprocess.run(
                        ["git", "-C", str(repo), "reset", "--quiet", "--mixed", resolved_new],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                else:
                    subprocess.run(
                        ["git", "-C", str(repo), "reset", "--hard", resolved_new],
                        check=True,
                        capture_output=True,
                        text=True,
                    )

            if refresh_index:
                diff_out = subprocess.check_output(
                    ["git", "-C", str(repo), "diff", "--name-only", "HEAD", "--", "."],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
                if not diff_out:
                    subprocess.run(
                        ["git", "-c", "core.safecrlf=false", "-C", str(repo), "add", "-u", "--", "."],
                        check=False,
                        capture_output=True,
                        text=True,
                    )

            journal_entry = replace(journal_entry, state="PUBLISHED_LEDGER_PENDING")
            self._write_journal_locked(journal_entry)
            record.last_heartbeat_epoch = now
            record.expires_at_epoch = min(
                now + DEFAULT_HEARTBEAT_WINDOW_S,
                hard_ceiling,
            )
            record.last_committed_digest = resolved_new
            self._write_ledger_locked(ledger)
            self._clear_journal_locked(
                journal_entry.tx_id, final_state="COMMITTED", entry=journal_entry
            )
            return resolved_new

    def release_workspace_lease(
        self,
        scopes: Sequence[str] | str,
        fencing_tokens: Mapping[str, str] | str | LeaseExecutionReport,
        final_status: str = "APPLIED",
        *,
        pid: int | None = None,
        holder_pid: int | None = None,
        holder: str | None = None,
    ) -> LeaseExecutionReport:
        """Transition `ACQUIRED -> RELEASING -> UNLOCKED` for matching fencing tokens."""
        if isinstance(scopes, str):
            raw_list = [s for s in scopes.split(",") if s.strip()]
        else:
            raw_list = list(scopes)
        canonical_scopes = tuple(dict.fromkeys(normalize_scope(s) for s in raw_list))

        target_pid: int | None = (
            int(pid)
            if pid is not None
            else (int(holder_pid) if holder_pid is not None else None)
        )
        target_holder: str | None = str(holder) if holder else None

        if isinstance(fencing_tokens, LeaseExecutionReport):
            if target_pid is None:
                target_pid = getattr(fencing_tokens.fencing_tokens, "_holder_pid", None)
            if target_holder is None:
                target_holder = (
                    getattr(fencing_tokens.fencing_tokens, "_holder", None)
                    or fencing_tokens.holder
                )
            token_map = {
                normalize_scope(k): str(v)
                for k, v in fencing_tokens.fencing_tokens.items()
            }
        elif isinstance(fencing_tokens, str):
            if target_pid is None:
                target_pid = getattr(fencing_tokens, "_holder_pid", None)
            if target_holder is None:
                target_holder = getattr(fencing_tokens, "_holder", None)
            token_map = {sc: str(fencing_tokens) for sc in canonical_scopes}
        else:
            if target_pid is None:
                target_pid = getattr(fencing_tokens, "_holder_pid", None)
            if target_holder is None:
                target_holder = getattr(fencing_tokens, "_holder", None)
            for v in fencing_tokens.values():
                if target_pid is None and getattr(v, "_holder_pid", None) is not None:
                    target_pid = getattr(v, "_holder_pid", None)
                if target_holder is None and getattr(v, "_holder", None) is not None:
                    target_holder = getattr(v, "_holder", None)
            token_map = {normalize_scope(k): str(v) for k, v in fencing_tokens.items()}

        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            self._recover_interrupted_commit_locked(ledger)
            released_scopes: list[str] = []
            for sc in canonical_scopes:
                rec = ledger.active_records.get(sc)
                expected_token = token_map.get(sc)
                if rec is None:
                    continue
                if expected_token and rec.fencing_token != expected_token:
                    continue
                if rec.mode == "shared_session" and rec.shared_holders:
                    removed = False
                    remaining_holders: list[dict[str, Any]] = []
                    for sh in rec.shared_holders:
                        sh_pid = int(sh.get("pid", sh.get("holder_pid", 0)))
                        sh_holder = str(sh.get("holder", ""))
                        if not removed:
                            if target_pid is not None and sh_pid == int(target_pid):
                                removed = True
                                continue
                            if (
                                target_pid is None
                                and target_holder is not None
                                and sh_holder == target_holder
                            ):
                                removed = True
                                continue
                        remaining_holders.append(sh)

                    if not removed and remaining_holders:
                        caller_pid = os.getpid()
                        for idx, sh in enumerate(remaining_holders):
                            if int(sh.get("pid", sh.get("holder_pid", 0))) == caller_pid:
                                remaining_holders.pop(idx)
                                removed = True
                                break
                        if not removed:
                            remaining_holders.pop(0)

                    rec.shared_holders = remaining_holders
                    if len(rec.shared_holders) > 0:
                        primary = rec.shared_holders[0]
                        rec.holder = str(primary.get("holder", rec.holder))
                        rec.holder_pid = int(
                            primary.get("pid", primary.get("holder_pid", rec.holder_pid))
                        )
                        rec.process_start_time_ns = int(
                            primary.get("process_start_time_ns", rec.process_start_time_ns)
                        )
                        rec.state = "ACQUIRED"
                        ledger.scope_states[sc] = "ACQUIRED"
                        released_scopes.append(sc)
                        continue

                rec.state = "RELEASING"
                ledger.scope_states[sc] = "RELEASING"
                ledger.active_records.pop(sc, None)
                ledger.history.append(replace(rec, state="UNLOCKED").to_dict())
                ledger.scope_states[sc] = "UNLOCKED"
                released_scopes.append(sc)
            self._write_ledger_locked(ledger)

        return LeaseExecutionReport(
            status=final_status,
            reason_code="LEASE_RELEASED",
            exit_code=EXIT_OK,
            scopes=tuple(released_scopes),
            fencing_tokens=token_map,
        )

    def get_status(self) -> dict[str, Any]:
        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            self._recover_interrupted_commit_locked(ledger)
            active_tx, journal_history = self._read_journal_locked()
            return {
                "ledger": ledger.to_dict(),
                "journal": {
                    "active_tx": active_tx.to_dict() if active_tx else None,
                    "history": journal_history[-20:],
                },
            }


def acquire_workspace_lease(
    scopes: Sequence[str] | str,
    mode: str = "exclusive_cutover",
    holder: str = "cli",
    ttl_s: float = MAX_LEASE_TTL_S,
    *,
    lease_dir: Path | str | None = None,
    state_db_path: Path | str | None = None,
    worktrees_root: Path | str | None = None,
    wait_seconds: float = 0.0,
) -> LeaseExecutionReport:
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        state_db_path=state_db_path,
        worktrees_root=worktrees_root,
    )
    return coord.acquire_workspace_lease(
        scopes=scopes,
        mode=mode,
        holder=holder,
        ttl_s=ttl_s,
        wait_seconds=wait_seconds,
    )


def atomic_fenced_commit(
    scope: str,
    acquired_token: str,
    staged_path: Path | str,
    target_path: Path | str,
    *,
    lease_dir: Path | str | None = None,
) -> str:
    coord = WorkspaceLeaseCoordinator(lease_dir=lease_dir)
    return coord.atomic_fenced_commit(
        scope=scope,
        acquired_token=acquired_token,
        staged_path=staged_path,
        target_path=target_path,
    )


def atomic_fenced_delete(
    target_path: Path | str,
    fencing_token: str = "",
    maybe_target_path: Path | str | None = None,
    *,
    scope: str | None = None,
    acquired_token: str | None = None,
    backup_path: Path | str | None = None,
    prune_empty_parents_up_to: Path | str | None = None,
    lease_dir: Path | str | None = None,
) -> str:
    coord = WorkspaceLeaseCoordinator(lease_dir=lease_dir)
    return coord.atomic_fenced_delete(
        target_path,
        fencing_token,
        maybe_target_path,
        scope=scope,
        acquired_token=acquired_token,
        backup_path=backup_path,
        prune_empty_parents_up_to=prune_empty_parents_up_to,
    )


def atomic_fenced_git_ref_update(
    scope: str,
    acquired_token: str,
    repo_root: Path | str,
    ref_name: str,
    new_sha: str,
    old_sha: str | None = None,
    *,
    reset_mode: str = "hard",
    fetch_from: str | None = None,
    fetch_refspec: str | None = None,
    refresh_index: bool = False,
    lease_dir: Path | str | None = None,
) -> str:
    coord = WorkspaceLeaseCoordinator(lease_dir=lease_dir)
    return coord.atomic_fenced_git_ref_update(
        scope=scope,
        acquired_token=acquired_token,
        repo_root=repo_root,
        ref_name=ref_name,
        new_sha=new_sha,
        old_sha=old_sha,
        reset_mode=reset_mode,
        fetch_from=fetch_from,
        fetch_refspec=fetch_refspec,
        refresh_index=refresh_index,
    )


def recover_interrupted_commit(
    *, lease_dir: Path | str | None = None
) -> list[CommitJournalRecord]:
    coord = WorkspaceLeaseCoordinator(lease_dir=lease_dir)
    return coord.recover_interrupted_commit()


def renew_lease_heartbeat(
    scope: str,
    acquired_token: str,
    now_epoch: float | None = None,
    *,
    lease_dir: Path | str | None = None,
) -> LeaseRecord:
    coord = WorkspaceLeaseCoordinator(lease_dir=lease_dir)
    return coord.renew_heartbeat(
        scope=scope,
        acquired_token=acquired_token,
        now_epoch=now_epoch,
    )


def release_workspace_lease(
    scopes: Sequence[str] | str,
    fencing_tokens: Mapping[str, str] | str | LeaseExecutionReport,
    final_status: str = "APPLIED",
    *,
    pid: int | None = None,
    holder_pid: int | None = None,
    holder: str | None = None,
    lease_dir: Path | str | None = None,
) -> LeaseExecutionReport:
    coord = WorkspaceLeaseCoordinator(lease_dir=lease_dir)
    return coord.release_workspace_lease(
        scopes=scopes,
        fencing_tokens=fencing_tokens,
        final_status=final_status,
        pid=pid,
        holder_pid=holder_pid,
        holder=holder,
    )


def _add_common_lease_Flags(
    parser: argparse.ArgumentParser, *, is_subparser: bool = False
) -> None:
    default_val = argparse.SUPPRESS if is_subparser else None
    parser.add_argument(
        "--lease-dir",
        dest="lease_dir",
        default=default_val,
        help="Directory containing workspace_lease.lock, workspace_leases.json, and workspace_lease.journal.json",
    )
    parser.add_argument(
        "--state-db",
        dest="state_db",
        default=default_val,
        help="Optional path to Hermes state.db for active-session contention checks",
    )
    parser.add_argument(
        "--worktrees-root",
        dest="worktrees_root",
        default=default_val,
        help="Optional path to .worktrees root for dirty-worktree contention checks",
    )


def configure_lease_subcommands(lease_parser: argparse.ArgumentParser) -> None:
    _add_common_lease_Flags(lease_parser, is_subparser=False)
    sub = lease_parser.add_subparsers(dest="lease_command")

    p_acq = sub.add_parser("acquire", help="Acquire a workspace concurrency lease")
    _add_common_lease_Flags(p_acq, is_subparser=True)
    p_acq.add_argument(
        "--scope",
        "--scopes",
        dest="scopes",
        action="append",
        required=True,
        help="Canonical scope(s), repeatable or comma-separated (e.g. repo:oss/crew,profile:*)",
    )
    p_acq.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        default="exclusive_cutover",
        help="Lease mode (default: exclusive_cutover)",
    )
    p_acq.add_argument("--holder", default="cli", help="Holder identifier")
    p_acq.add_argument("--ttl-s", type=float, default=MAX_LEASE_TTL_S, help="Max lease TTL in seconds")
    p_acq.add_argument(
        "--wait-seconds",
        "--WaitSeconds",
        dest="wait_seconds",
        type=float,
        default=0.0,
        help="Bounded manual poll wait in seconds before deferring (exit 75)",
    )
    p_acq.add_argument("--poll-interval-s", type=float, default=2.0)

    p_commit = sub.add_parser("commit", help="Journaled lock-held atomic fenced commit")
    _add_common_lease_Flags(p_commit, is_subparser=True)
    p_commit.add_argument("--scope", required=True)
    p_commit.add_argument("--token", required=True)
    p_commit.add_argument("--staged", required=True)
    p_commit.add_argument("--target", required=True)

    p_del = sub.add_parser("delete", help="Journaled lock-held atomic fenced deletion")
    _add_common_lease_Flags(p_del, is_subparser=True)
    p_del.add_argument("--scope", default=None)
    p_del.add_argument(
        "--fencing-token",
        "--token",
        dest="fencing_token",
        required=True,
    )
    p_del.add_argument(
        "--path",
        "--target",
        dest="target_path",
        required=True,
    )
    p_del.add_argument(
        "--backup-path",
        "--quarantine-path",
        dest="backup_path",
        default=None,
    )
    p_del.add_argument(
        "--prune-empty-parents-up-to",
        dest="prune_empty_parents_up_to",
        default=None,
    )

    p_git = sub.add_parser("git-ref-update", help="Journaled lock-held git update-ref + reset")
    _add_common_lease_Flags(p_git, is_subparser=True)
    p_git.add_argument("--scope", required=True)
    p_git.add_argument("--token", required=True)
    p_git.add_argument("--repo-root", required=True)
    p_git.add_argument("--ref", default="refs/heads/main")
    p_git.add_argument("--new-sha", required=True)
    p_git.add_argument("--old-sha", default=None)
    p_git.add_argument("--reset-mode", choices=("hard", "mixed"), default="hard")
    p_git.add_argument("--fetch-from", default=None)
    p_git.add_argument("--fetch-refspec", default=None)
    p_git.add_argument("--refresh-index", action="store_true", default=False)

    p_hb = sub.add_parser("heartbeat", help="Renew 30s lease heartbeat")
    _add_common_lease_Flags(p_hb, is_subparser=True)
    p_hb.add_argument("--scope", required=True)
    p_hb.add_argument("--token", required=True)

    p_rel = sub.add_parser("release", help="Release held workspace lease(s)")
    _add_common_lease_Flags(p_rel, is_subparser=True)
    p_rel.add_argument(
        "--scope",
        "--scopes",
        dest="scopes",
        action="append",
        required=True,
    )
    p_rel.add_argument("--token", default="")
    p_rel.add_argument("--tokens-json", default="")
    p_rel.add_argument("--status", default="APPLIED")
    p_rel.add_argument("--pid", type=int, default=None)
    p_rel.add_argument("--holder", default=None)

    p_rec = sub.add_parser("recover", help="Reconcile interrupted commit journal entries")
    _add_common_lease_Flags(p_rec, is_subparser=True)

    p_stat = sub.add_parser("status", help="Inspect current workspace lease ledger and commit journal")
    _add_common_lease_Flags(p_stat, is_subparser=True)


def build_lease_parser(subparsers: Any) -> argparse.ArgumentParser:
    lease_parser = subparsers.add_parser(
        "lease",
        help="Coordinate workspace concurrency leases and fenced atomic commits",
        description="Workspace Lease Coordinator (UNLOCKED -> ACQUIRING -> ACQUIRED -> RELEASING / EXPIRED / PREEMPTED).",
    )
    configure_lease_subcommands(lease_parser)
    lease_parser.set_defaults(func=cmd_lease)
    return lease_parser


def cmd_lease(args: argparse.Namespace) -> int:
    coord = WorkspaceLeaseCoordinator(
        lease_dir=getattr(args, "lease_dir", None),
        state_db_path=getattr(args, "state_db", None),
        worktrees_root=getattr(args, "worktrees_root", None),
    )
    subcmd = getattr(args, "lease_command", None) or "status"
    try:
        if subcmd == "acquire":
            raw_scopes: list[str] = []
            for item in getattr(args, "scopes", []) or []:
                raw_scopes.extend(part.strip() for part in str(item).split(",") if part.strip())
            report = coord.acquire_workspace_lease(
                scopes=raw_scopes,
                mode=getattr(args, "mode", "exclusive_cutover"),
                holder=getattr(args, "holder", "cli"),
                ttl_s=float(getattr(args, "ttl_s", MAX_LEASE_TTL_S)),
                wait_seconds=float(getattr(args, "wait_seconds", 0.0)),
                poll_interval_s=float(getattr(args, "poll_interval_s", 2.0)),
            )
            print(json.dumps(report.to_dict()))
            return int(report.exit_code)

        if subcmd == "commit":
            digest = coord.atomic_fenced_commit(
                scope=args.scope,
                acquired_token=args.token,
                staged_path=args.staged,
                target_path=args.target,
            )
            print(
                json.dumps(
                    {
                        "status": "COMMITTED",
                        "reason_code": "OK",
                        "exit_code": EXIT_OK,
                        "scope": normalize_scope(args.scope),
                        "fencing_token": args.token,
                        "target_path": str(args.target),
                        "staged_digest": digest,
                    }
                )
            )
            return EXIT_OK

        if subcmd == "delete":
            resolved_scope = (
                normalize_scope(args.scope)
                if getattr(args, "scope", None)
                else _extract_scope_from_token(args.fencing_token)
            )
            digest = coord.atomic_fenced_delete(
                target_path=args.target_path,
                fencing_token=args.fencing_token,
                scope=resolved_scope,
                backup_path=getattr(args, "backup_path", None),
                prune_empty_parents_up_to=getattr(args, "prune_empty_parents_up_to", None),
            )
            print(
                json.dumps(
                    {
                        "status": "COMMITTED",
                        "reason_code": "OK",
                        "exit_code": EXIT_OK,
                        "scope": resolved_scope,
                        "fencing_token": args.fencing_token,
                        "target_path": str(args.target_path),
                        "staged_digest": digest,
                    }
                )
            )
            return EXIT_OK

        if subcmd == "git-ref-update":
            updated_sha = coord.atomic_fenced_git_ref_update(
                scope=args.scope,
                acquired_token=args.token,
                repo_root=args.repo_root,
                ref_name=args.ref,
                new_sha=args.new_sha,
                old_sha=args.old_sha,
                reset_mode=getattr(args, "reset_mode", "hard"),
                fetch_from=getattr(args, "fetch_from", None),
                fetch_refspec=getattr(args, "fetch_refspec", None),
                refresh_index=bool(getattr(args, "refresh_index", False)),
            )
            print(
                json.dumps(
                    {
                        "status": "COMMITTED",
                        "reason_code": "OK",
                        "exit_code": EXIT_OK,
                        "scope": normalize_scope(args.scope),
                        "fencing_token": args.token,
                        "repo_root": str(args.repo_root),
                        "ref": args.ref,
                        "new_sha": updated_sha,
                    }
                )
            )
            return EXIT_OK

        if subcmd == "heartbeat":
            rec = coord.renew_heartbeat(scope=args.scope, acquired_token=args.token)
            print(json.dumps({"status": "RENEWED", "exit_code": EXIT_OK, "record": rec.to_dict()}))
            return EXIT_OK

        if subcmd == "release":
            raw_scopes = []
            for item in getattr(args, "scopes", []) or []:
                raw_scopes.extend(part.strip() for part in str(item).split(",") if part.strip())
            tokens_arg: Mapping[str, str] | str
            if getattr(args, "tokens_json", ""):
                tokens_arg = json.loads(args.tokens_json)
            else:
                tokens_arg = str(getattr(args, "token", ""))
            report = coord.release_workspace_lease(
                scopes=raw_scopes,
                fencing_tokens=tokens_arg,
                final_status=str(getattr(args, "status", "APPLIED")),
                pid=getattr(args, "pid", None),
                holder=getattr(args, "holder", None),
            )
            print(json.dumps(report.to_dict()))
            return int(report.exit_code)

        if subcmd == "recover":
            records = coord.recover_interrupted_commit()
            print(
                json.dumps(
                    {
                        "status": "RECOVERED",
                        "exit_code": EXIT_OK,
                        "recovered": [r.to_dict() for r in records],
                    }
                )
            )
            return EXIT_OK

        status_payload = coord.get_status()
        print(json.dumps(status_payload))
        return EXIT_OK

    except LeasePreemptedError as exc:
        print(
            json.dumps(
                {
                    "status": "PREEMPTED",
                    "reason_code": exc.reason_code,
                    "exit_code": exc.exit_code,
                    "fencing_token": exc.fencing_token,
                    "staged_path": exc.staged_path,
                    "message": str(exc),
                }
            )
        )
        return int(exc.exit_code)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "reason_code": "CUTOVER_ERROR",
                    "exit_code": EXIT_ERROR,
                    "message": str(exc),
                }
            )
        )
        return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes lease")
    configure_lease_subcommands(parser)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return cmd_lease(args)


if __name__ == "__main__":
    sys.exit(main())
