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
from dataclasses import replace
import hashlib
import json
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

if __package__ in (None, ""):
    # Script-mode execution (`python hermes_cli/workspace_lease.py ...` from the cutover
    # wrappers): make this checkout's `hermes_cli` package importable ahead of any
    # editable-installed sibling checkout so the companion modules resolve to this tree.
    _repo_root = str(Path(__file__).resolve().parents[1])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

from hermes_cli.workspace_lease_git import (
    execute_atomic_fenced_git_ref_update,
    recover_git_ref_journal_locked,
)
from hermes_cli.workspace_lease_types import (
    ACTIVE_SESSION_WINDOW_S,
    DEFAULT_HEARTBEAT_INTERVAL_S,
    DEFAULT_HEARTBEAT_WINDOW_S,
    DEFAULT_MAX_TTL_S,
    EX_DATAERR,
    EX_TEMPFAIL,
    EXIT_DATAERR,
    EXIT_DEFERRED,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PREEMPTED,
    JOURNAL_STATES,
    LEASE_STATES,
    MAX_LEASE_TTL_S,
    VALID_MODES,
    VALID_NAMESPACES,
    CommitJournalRecord,
    CorruptJournalError,
    CorruptPersistentStateError,
    LeaseExecutionReport,
    LeasePreemptedError,
    LeaseRecord,
    _clamp_ttl_s,
    _extract_scope_from_token,
    _format_holder_id,
    _LeaseLedger,
    _LeaseTokenMap,
    _LeaseTokenStr,
    _make_token_map,
    _make_token_str,
    _split_token_and_holder_id,
    _token_to_slug,
    normalize_scope,
    scopes_overlap,
    token_to_slug,
)

__all__ = [
    "ACTIVE_SESSION_WINDOW_S",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "DEFAULT_HEARTBEAT_WINDOW_S",
    "DEFAULT_MAX_TTL_S",
    "EXIT_DATAERR",
    "EXIT_DEFERRED",
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_PREEMPTED",
    "EX_DATAERR",
    "EX_TEMPFAIL",
    "JOURNAL_STATES",
    "LEASE_STATES",
    "MAX_LEASE_TTL_S",
    "VALID_MODES",
    "VALID_NAMESPACES",
    "CommitJournalRecord",
    "CorruptJournalError",
    "CorruptPersistentStateError",
    "LeaseExecutionReport",
    "LeasePreemptedError",
    "LeaseRecord",
    "WorkspaceLeaseCoordinator",
    "acquire_workspace_lease",
    "atomic_fenced_commit",
    "atomic_fenced_delete",
    "atomic_fenced_git_ref_update",
    "build_lease_parser",
    "cmd_lease",
    "configure_lease_subcommands",
    "main",
    "normalize_scope",
    "recover_interrupted_commit",
    "release_workspace_lease",
    "renew_lease_heartbeat",
    "scopes_overlap",
    "token_to_slug",
]

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
        """Load `workspace_leases.json`; malformed on-disk state fails closed and is never overwritten."""
        default_epoch = int(self._now())
        if not self.ledger_path.is_file():
            return _LeaseLedger(epoch=default_epoch)
        try:
            raw = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            raise CorruptPersistentStateError(
                f"Corrupt ledger file at {self.ledger_path}: {err}",
                path=str(self.ledger_path),
            ) from err
        if not isinstance(raw, Mapping):
            raise CorruptPersistentStateError(
                f"Corrupt ledger file at {self.ledger_path}: top-level JSON must be an object, "
                f"got {type(raw).__name__}",
                path=str(self.ledger_path),
            )
        try:
            return _LeaseLedger.from_dict(raw, default_epoch=default_epoch)
        except (TypeError, ValueError, AttributeError) as err:
            raise CorruptPersistentStateError(
                f"Corrupt ledger file at {self.ledger_path}: {err}",
                path=str(self.ledger_path),
            ) from err

    def _write_ledger_locked(self, ledger: _LeaseLedger) -> None:
        _atomic_write_json(self.ledger_path, ledger.to_dict())

    def _read_journal_locked(self) -> tuple[CommitJournalRecord | None, list[dict[str, Any]]]:
        """Load `workspace_lease.journal.json`; malformed journal state fails closed and is never overwritten."""
        if not self.journal_path.is_file():
            return None, []
        try:
            raw = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            raise CorruptJournalError(
                f"Corrupt journal file at {self.journal_path}: {err}",
                path=str(self.journal_path),
            ) from err
        if not isinstance(raw, Mapping):
            raise CorruptJournalError(
                f"Corrupt journal file at {self.journal_path}: top-level JSON must be an object, "
                f"got {type(raw).__name__}",
                path=str(self.journal_path),
            )
        raw_history = raw.get("history")
        if raw_history is not None and not isinstance(raw_history, list):
            raise CorruptJournalError(
                f"Corrupt journal file at {self.journal_path}: history must be a list",
                path=str(self.journal_path),
            )
        history = list(raw_history or [])
        active_raw = raw.get("active_tx")
        if active_raw is not None and not isinstance(active_raw, Mapping):
            raise CorruptJournalError(
                f"Corrupt journal file at {self.journal_path}: active_tx must be an object or null",
                path=str(self.journal_path),
            )
        try:
            if isinstance(active_raw, Mapping) and active_raw.get("tx_id"):
                return CommitJournalRecord.from_dict(active_raw), history
            if raw.get("tx_id") and raw.get("state") in ("PUBLISH_INTENT", "PUBLISHED_LEDGER_PENDING"):
                return CommitJournalRecord.from_dict(raw), history
        except (TypeError, ValueError) as err:
            raise CorruptJournalError(
                f"Corrupt journal file at {self.journal_path}: {err}",
                path=str(self.journal_path),
            ) from err
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
            return recover_git_ref_journal_locked(self, ledger, active_tx, now)

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

    def _probe_state_db_sessions_locked(
        self, now: float
    ) -> tuple[str, dict[str, Any]] | None:
        """Probe the configured `state.db` for active turns; any probe failure defers (fail closed).

        Supports both the design-time session schema (`status`, `updated_at`) and the live Hermes
        schema (`ended_at`, `last_activity_at`). A configured-but-unqueryable database (missing file,
        not SQLite, missing `sessions` table, or missing activity columns) MUST NOT be treated as
        "no contention"; it yields `STATE_DB_QUERY_FAILED` so the cutover defers with exit 75.
        """
        db_path = self.state_db_path
        if db_path is None:
            return None
        failure: dict[str, Any] = {"state_db_path": str(db_path)}
        if not db_path.is_file():
            failure["error"] = "configured state.db path does not exist or is not a file"
            return ("STATE_DB_QUERY_FAILED", failure)
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1.0)
        except sqlite3.Error as err:
            failure["error"] = f"sqlite open failed: {err}"
            return ("STATE_DB_QUERY_FAILED", failure)
        try:
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "session_turn_leases" in tables:
                turn_sql = (
                    "SELECT conversation_id, holder, expires_at FROM session_turn_leases "
                    "WHERE expires_at >= ? LIMIT 1"
                )
                turn_row = conn.execute(turn_sql, (now,)).fetchone()
                if turn_row is not None:
                    return (
                        "DEFERRED_ACTIVE_SESSION",
                        {"session_id": turn_row[0], "status": "running_turn", "holder": turn_row[1], "expires_at": turn_row[2]},
                    )
                return None

            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(sessions)")}
            if not columns:
                failure["error"] = "state.db is missing the required `sessions` table"
                return ("STATE_DB_QUERY_FAILED", failure)
            cutoff = now - ACTIVE_SESSION_WINDOW_S
            if {"status", "updated_at"} <= columns:
                sql = (
                    "SELECT id, status, updated_at FROM sessions "
                    "WHERE lower(status) IN ('running', 'active', 'in_progress') "
                    "AND updated_at >= ? LIMIT 1"
                )
            elif {"ended_at", "last_activity_at"} <= columns:
                sql = (
                    "SELECT id, 'running', last_activity_at FROM sessions "
                    "WHERE ended_at IS NULL AND last_activity_at >= ? LIMIT 1"
                )
            else:
                failure["error"] = (
                    "sessions table lacks activity columns "
                    "(expected status/updated_at or ended_at/last_activity_at)"
                )
                return ("STATE_DB_QUERY_FAILED", failure)
            row = conn.execute(sql, (cutoff,)).fetchone()
        except sqlite3.Error as err:
            failure["error"] = f"sessions query failed: {err}"
            return ("STATE_DB_QUERY_FAILED", failure)
        finally:
            conn.close()
        if row is not None:
            return (
                "DEFERRED_ACTIVE_SESSION",
                {"session_id": row[0], "status": row[1], "updated_at": row[2]},
            )
        return None

    @staticmethod
    def _resolve_worktree_repo_scope(wt_dir: Path) -> str | None:
        try:
            raw = subprocess.check_output(
                ["git", "-C", str(wt_dir), "rev-parse", "--git-common-dir"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            ).strip()
            common_path = Path(raw)
            if not common_path.is_absolute():
                common_path = (wt_dir / common_path).resolve()
            else:
                common_path = common_path.resolve()
            posix = common_path.as_posix()
            if ".git/modules/" in posix:
                sub_part = posix.split(".git/modules/", 1)[1]
                return f"repo:{sub_part}"
            if posix.endswith("/.git"):
                parent_posix = posix[:-5]
                if "/workspaces/" in parent_posix:
                    rel = parent_posix.split("/workspaces/", 1)[1]
                    if rel:
                        return f"repo:{rel}"
                    return "repo:root"
                return f"repo:{Path(parent_posix).name}"
            return None
        except Exception:
            return None

    def _check_external_contention_locked(
        self, now: float, canonical_scopes: Sequence[str] = ()
    ) -> tuple[str, dict[str, Any]] | None:
        state_db_result = self._probe_state_db_sessions_locked(now)
        if state_db_result is not None:
            return state_db_result

        if self.worktrees_root is not None and self.worktrees_root.is_dir():
            target_repo_scopes = [
                sc for sc in canonical_scopes if sc.startswith("repo:")
            ]
            if not target_repo_scopes:
                return None
            for wt_dir in sorted(self.worktrees_root.glob("wt-*")):
                if not wt_dir.is_dir():
                    continue
                wt_scope = self._resolve_worktree_repo_scope(wt_dir)
                if wt_scope is None or not any(
                    scopes_overlap(req, wt_scope) for req in target_repo_scopes
                ):
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
                ext_conflict = self._check_external_contention_locked(
                    now, canonical_scopes
                )
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
            holder_id = _format_holder_id(holder_pid, process_start_time_ns, holder)
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
                    holder_token = f"{existing.fencing_token}@{holder_id}"
                    existing.shared_holders = [
                        sh
                        for sh in existing.shared_holders
                        if not (
                            str(sh.get("holder_id", "")) == holder_id
                            or (
                                int(sh.get("pid", sh.get("holder_pid", 0))) == holder_pid
                                and int(sh.get("process_start_time_ns", 0)) == process_start_time_ns
                                and str(sh.get("holder", "")) == holder
                            )
                        )
                    ]
                    existing.shared_holders.append(
                        {
                            "holder_id": holder_id,
                            "holder": holder,
                            "pid": holder_pid,
                            "holder_pid": holder_pid,
                            "process_start_time_ns": process_start_time_ns,
                            "fencing_token": holder_token,
                            "acquired_at_epoch": now,
                        }
                    )
                    fencing_tokens[sc] = _make_token_str(
                        holder_token,
                        holder_id=holder_id,
                        holder_pid=holder_pid,
                        holder=holder,
                    )
                    ledger.scope_states[sc] = "ACQUIRED"
                    continue

                gen = int(ledger.scope_generations.get(sc, 0)) + 1
                ledger.scope_generations[sc] = gen
                token = f"{sc}:{ledger.epoch}:{gen}"
                issued_token = (
                    f"{token}@{holder_id}" if mode == "shared_session" else token
                )
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
                                "holder_id": holder_id,
                                "holder": holder,
                                "pid": holder_pid,
                                "holder_pid": holder_pid,
                                "process_start_time_ns": process_start_time_ns,
                                "fencing_token": issued_token,
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
                    issued_token,
                    holder_id=holder_id,
                    holder_pid=holder_pid,
                    holder=holder,
                )

            self._write_ledger_locked(ledger)
            wrapped_tokens = _make_token_map(
                fencing_tokens,
                holder_id=holder_id,
                holder_pid=holder_pid,
                holder=holder,
            )
            primary_token = (
                wrapped_tokens[canonical_scopes[0]]
                if canonical_scopes
                else _make_token_str(
                    "", holder_id=holder_id, holder_pid=holder_pid, holder=holder
                )
            )
            return LeaseExecutionReport(
                status="ACQUIRED",
                reason_code="LEASE_ACQUIRED",
                exit_code=EXIT_OK,
                scopes=canonical_scopes,
                mode=mode,
                holder=holder,
                holder_id=holder_id,
                holder_pid=holder_pid,
                process_start_time_ns=process_start_time_ns,
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
        base_token, _ = _split_token_and_holder_id(acquired_token)
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
                or record.fencing_token != base_token
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
        stash_dirty: bool = False,
        stash_message: str | None = None,
    ) -> str:
        """Execute a fenced, journaled Git ref fast-forward + working-tree sync inside `workspace_lease.lock`.

        Reset contracts:
        - `reset_mode="hard"` (default publication contract, A-01R/A-02): updates `ref_name` to `new_sha`,
          reconciles both the Git index and tracked working-tree bytes via `git reset --hard <new_sha>`,
          and when `stash_dirty=True` captures dirty tracked modifications before ref mutation into a
          journaled stash commit (`stash_sha`) and restores them onto `<new_sha>` inside the same
          coordinator-owned recoverable transaction.
        - `reset_mode="mixed"` (overlay-preserving plugin alignment contract, S-04): updates `ref_name`
          and aligns `HEAD`/index to `new_sha` via `git reset --quiet --mixed <new_sha>` while intentionally
          leaving deployed working-tree overlay bytes untouched on disk.
        """
        return execute_atomic_fenced_git_ref_update(
            self,
            scope,
            acquired_token,
            repo_root,
            ref_name,
            new_sha,
            old_sha,
            reset_mode=reset_mode,
            fetch_from=fetch_from,
            fetch_refspec=fetch_refspec,
            refresh_index=refresh_index,
            stash_dirty=stash_dirty,
            stash_message=stash_message,
        )

    def release_workspace_lease(
        self,
        scopes: Sequence[str] | str,
        fencing_tokens: Mapping[str, Any] | str | LeaseExecutionReport = "",
        final_status: str = "APPLIED",
        *,
        holder_id: str | None = None,
        pid: int | None = None,
        holder_pid: int | None = None,
        holder: str | None = None,
    ) -> LeaseExecutionReport:
        """Transition `ACQUIRED -> RELEASING -> UNLOCKED` for matching fencing tokens.

        For `shared_session` leases, each holder is identified by a durable `holder_id`
        (`"<pid>:<start_ns>:<holder>"`) embedded in the holder's fencing token
        (`<base_token>@<holder_id>`) and/or supplied explicitly via `holder_id`, `pid`,
        or `holder`. Release NEVER guesses `os.getpid()` and NEVER removes an arbitrary
        sibling (`pop(0)`); if the requested identity is absent or unknown, all existing
        shared holders remain intact.
        """
        if isinstance(scopes, str):
            raw_list = [s for s in scopes.split(",") if s.strip()]
        else:
            raw_list = list(scopes)
        canonical_scopes = tuple(dict.fromkeys(normalize_scope(s) for s in raw_list))

        explicit_holder_id: str | None = str(holder_id) if holder_id else None
        explicit_pid: int | None = (
            int(pid)
            if pid is not None
            else (int(holder_pid) if holder_pid is not None else None)
        )
        explicit_holder: str | None = str(holder) if holder else None

        implicit_holder_id: str | None = None
        implicit_pid: int | None = None
        implicit_holder: str | None = None

        if isinstance(fencing_tokens, LeaseExecutionReport):
            implicit_holder_id = (
                fencing_tokens.holder_id
                or getattr(fencing_tokens.fencing_tokens, "_holder_id", None)
            )
            implicit_pid = (
                fencing_tokens.holder_pid
                or getattr(fencing_tokens.fencing_tokens, "_holder_pid", None)
            )
            implicit_holder = (
                getattr(fencing_tokens.fencing_tokens, "_holder", None)
                or fencing_tokens.holder
            )
            token_map = {
                normalize_scope(k): str(v)
                for k, v in fencing_tokens.fencing_tokens.items()
            }
        elif isinstance(fencing_tokens, str):
            implicit_holder_id = getattr(fencing_tokens, "_holder_id", None)
            implicit_pid = getattr(fencing_tokens, "_holder_pid", None)
            implicit_holder = getattr(fencing_tokens, "_holder", None)
            token_map = (
                {sc: str(fencing_tokens) for sc in canonical_scopes}
                if fencing_tokens
                else {}
            )
        elif isinstance(fencing_tokens, Mapping):
            implicit_holder_id = getattr(fencing_tokens, "_holder_id", None)
            implicit_pid = getattr(fencing_tokens, "_holder_pid", None)
            implicit_holder = getattr(fencing_tokens, "_holder", None)
            if "fencing_tokens" in fencing_tokens and isinstance(
                fencing_tokens.get("fencing_tokens"), Mapping
            ):
                if not implicit_holder_id and fencing_tokens.get("holder_id"):
                    implicit_holder_id = str(fencing_tokens["holder_id"])
                if implicit_pid is None and fencing_tokens.get("holder_pid"):
                    implicit_pid = int(fencing_tokens["holder_pid"])
                if not implicit_holder and fencing_tokens.get("holder"):
                    implicit_holder = str(fencing_tokens["holder"])
                inner_tokens = fencing_tokens["fencing_tokens"]
                token_map = {
                    normalize_scope(str(k)): str(v) for k, v in inner_tokens.items()
                }
            else:
                for v in fencing_tokens.values():
                    if implicit_holder_id is None and getattr(v, "_holder_id", None) is not None:
                        implicit_holder_id = getattr(v, "_holder_id", None)
                    if implicit_pid is None and getattr(v, "_holder_pid", None) is not None:
                        implicit_pid = getattr(v, "_holder_pid", None)
                    if implicit_holder is None and getattr(v, "_holder", None) is not None:
                        implicit_holder = getattr(v, "_holder", None)
                token_map = {
                    normalize_scope(str(k)): str(v) for k, v in fencing_tokens.items()
                }
        else:
            token_map = {}

        with self._acquire_kernel_mutex(timeout_s=5.0):
            ledger = self._read_ledger_locked()
            self._recover_interrupted_commit_locked(ledger)
            # Atomic batch pre-validation (S-05R): validate EVERY requested scope before any
            # record is mutated. If any scope fails, the whole release is a NOOP.
            unmatched_reason, plan = self._plan_release_batch_locked(
                ledger,
                canonical_scopes,
                token_map,
                explicit_holder_id=explicit_holder_id,
                explicit_pid=explicit_pid,
                explicit_holder=explicit_holder,
                implicit_holder_id=implicit_holder_id,
                implicit_pid=implicit_pid,
                implicit_holder=implicit_holder,
            )
            released_scopes: list[str] = []
            if unmatched_reason is None:
                for sc, remaining_holders in plan:
                    rec = ledger.active_records[sc]
                    if remaining_holders:
                        rec.shared_holders = remaining_holders
                        primary = remaining_holders[0]
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

        is_released = unmatched_reason is None and bool(released_scopes)
        return LeaseExecutionReport(
            status=final_status if is_released else "NOOP",
            reason_code="LEASE_RELEASED" if is_released else (unmatched_reason or "LEASE_NOT_ACTIVE"),
            exit_code=EXIT_OK,
            scopes=tuple(released_scopes) if is_released else (),
            fencing_tokens=token_map,
        )

    def _plan_release_batch_locked(
        self,
        ledger: _LeaseLedger,
        canonical_scopes: tuple[str, ...],
        token_map: Mapping[str, str],
        *,
        explicit_holder_id: str | None,
        explicit_pid: int | None,
        explicit_holder: str | None,
        implicit_holder_id: str | None,
        implicit_pid: int | None,
        implicit_holder: str | None,
    ) -> tuple[str | None, list[tuple[str, list[dict[str, Any]]]]]:
        """Validate every requested scope without mutating the ledger.

        Returns `(None, plan)` when ALL scopes are releasable, where each plan entry is
        `(scope, remaining_shared_holders)` (`[]` means the record is removed entirely).
        Returns `(reason_code, [])` on the first failing scope so the caller fails closed.
        """
        if not canonical_scopes:
            return "INVALID_FENCING_TOKEN", []
        has_explicit_selector = (
            explicit_holder_id is not None
            or explicit_pid is not None
            or explicit_holder is not None
        )
        plan: list[tuple[str, list[dict[str, Any]]]] = []
        for sc in canonical_scopes:
            expected_base, token_holder_id = _split_token_and_holder_id(token_map.get(sc, ""))
            if not expected_base:
                return "INVALID_FENCING_TOKEN", []
            rec = ledger.active_records.get(sc)
            if rec is None:
                return "LEASE_NOT_ACTIVE", []
            if rec.fencing_token != expected_base:
                return "INVALID_FENCING_TOKEN", []
            if rec.mode != "shared_session" or not rec.shared_holders:
                plan.append((sc, []))
                continue

            candidate_holder_id = token_holder_id or implicit_holder_id
            if (
                not has_explicit_selector
                and not candidate_holder_id
                and implicit_pid is None
                and not implicit_holder
            ):
                return "SHARED_HOLDER_ID_REQUIRED", []

            removed = False
            remaining_holders: list[dict[str, Any]] = []
            for sh in rec.shared_holders:
                sh_pid = int(sh.get("pid", sh.get("holder_pid", 0)))
                sh_start_ns = int(sh.get("process_start_time_ns", 0))
                sh_holder = str(sh.get("holder", ""))
                sh_holder_id = str(
                    sh.get("holder_id") or _format_holder_id(sh_pid, sh_start_ns, sh_holder)
                )
                if removed:
                    remaining_holders.append(sh)
                    continue
                if has_explicit_selector:
                    matched = (
                        (explicit_holder_id is None or sh_holder_id == explicit_holder_id)
                        and (explicit_pid is None or sh_pid == explicit_pid)
                        and (explicit_holder is None or sh_holder == explicit_holder)
                    )
                elif candidate_holder_id is not None:
                    matched = sh_holder_id == candidate_holder_id
                else:
                    matched = (
                        (implicit_pid is None or sh_pid == int(implicit_pid))
                        and (implicit_holder is None or sh_holder == implicit_holder)
                    )
                if matched:
                    removed = True
                    continue
                remaining_holders.append(sh)
            if not removed:
                return "HOLDER_NOT_FOUND", []
            plan.append((sc, remaining_holders))
        return None, plan

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
    stash_dirty: bool = False,
    stash_message: str | None = None,
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
        stash_dirty=stash_dirty,
        stash_message=stash_message,
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
    fencing_tokens: Mapping[str, Any] | str | LeaseExecutionReport = "",
    final_status: str = "APPLIED",
    *,
    holder_id: str | None = None,
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
        holder_id=holder_id,
        pid=pid,
        holder_pid=holder_pid,
        holder=holder,
    )


def configure_lease_subcommands(lease_parser: argparse.ArgumentParser) -> None:
    """Attach `hermes lease` subcommands (implemented in `workspace_lease_cli`)."""
    from hermes_cli.workspace_lease_cli import configure_lease_subcommands as _impl

    _impl(lease_parser)


def build_lease_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the `hermes lease` parser on a parent subparsers action."""
    from hermes_cli.workspace_lease_cli import build_lease_parser as _impl

    return _impl(subparsers)


def cmd_lease(args: argparse.Namespace) -> int:
    """Dispatch a parsed `hermes lease` namespace (exit codes 0/1/75/78/79)."""
    from hermes_cli.workspace_lease_cli import cmd_lease as _impl

    return _impl(args)


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point used by the cutover wrappers (`python workspace_lease.py ...`)."""
    from hermes_cli.workspace_lease_cli import main as _impl

    return _impl(argv)


if __name__ == "__main__":
    sys.exit(main())
