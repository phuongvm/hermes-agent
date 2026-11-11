"""Git ref update, mixed/hard reset, and stash lifecycle helpers for WorkspaceLeaseCoordinator."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

if __package__ in (None, ""):
    _repo_root = str(Path(__file__).resolve().parents[1])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

from hermes_cli.workspace_lease_types import (
    DEFAULT_HEARTBEAT_WINDOW_S,
    MAX_LEASE_TTL_S,
    CommitJournalRecord,
    LeasePreemptedError,
    _LeaseLedger,
    _token_to_slug,
    normalize_scope,
)

if TYPE_CHECKING:
    from hermes_cli.workspace_lease import WorkspaceLeaseCoordinator


def _reconcile_git_worktree_and_stash(
    repo_root: Path,
    target_sha: str,
    *,
    reset_mode: str,
    stash_sha: str,
    stash_message: str,
    refresh_index: bool,
    error_context: str,
) -> None:
    """Apply `git reset --mixed|--hard`, optional `git stash apply`, and optional index refresh."""
    if reset_mode == "mixed":
        subprocess.run(
            ["git", "-C", str(repo_root), "reset", "--quiet", "--mixed", target_sha],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["git", "-C", str(repo_root), "reset", "--hard", target_sha],
            check=True,
            capture_output=True,
            text=True,
        )
        if stash_sha:
            apply_res = subprocess.run(
                ["git", "-C", str(repo_root), "stash", "apply", "--quiet", stash_sha],
                check=False,
                capture_output=True,
                text=True,
            )
            if apply_res.returncode != 0:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo_root),
                        "stash",
                        "store",
                        "-m",
                        stash_message,
                        stash_sha,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                raise RuntimeError(
                    f"git stash apply failed {error_context} "
                    f"(saved in git stash list as {stash_message!r}): {apply_res.stderr.strip()}"
                )

    if refresh_index:
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


def recover_git_ref_journal_locked(
    coord: WorkspaceLeaseCoordinator,
    ledger: _LeaseLedger,
    active_tx: CommitJournalRecord,
    now: float,
) -> list[CommitJournalRecord]:
    """Recover an interrupted `kind == 'git_ref'` journal transaction under `workspace_lease.lock`."""
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
        stash_sha = getattr(active_tx, "stash_sha", "") or ""
        stash_msg = (
            getattr(active_tx, "stash_message", "")
            or f"recovered-stash-{active_tx.tx_id}"
        )
        _reconcile_git_worktree_and_stash(
            repo_root,
            active_tx.staged_digest,
            reset_mode=reset_mode,
            stash_sha=stash_sha,
            stash_message=stash_msg,
            refresh_index=bool(getattr(active_tx, "refresh_index", False)),
            error_context=f"during recovery of {active_tx.tx_id}",
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
        coord._write_ledger_locked(ledger)
        done = coord._clear_journal_locked(
            active_tx.tx_id, final_state="COMMITTED", entry=active_tx
        )
        return [done] if done else []

    done = coord._clear_journal_locked(
        active_tx.tx_id, final_state="ABORTED", entry=active_tx
    )
    return [done] if done else []


def execute_atomic_fenced_git_ref_update(
    coord: WorkspaceLeaseCoordinator,
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
    """Execute a fenced, journaled Git ref fast-forward + working-tree sync inside `workspace_lease.lock`."""
    repo = Path(repo_root)
    if reset_mode not in ("hard", "mixed"):
        raise ValueError(
            f"Unsupported git reset_mode {reset_mode!r}: expected 'hard' or 'mixed'"
        )
    with coord._acquire_kernel_mutex(timeout_s=5.0):
        ledger = coord._read_ledger_locked()
        coord._recover_interrupted_commit_locked(ledger)
        canonical = normalize_scope(scope)
        record = ledger.active_records.get(canonical)
        now = coord._now()
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
            fetch_cmd = [
                "git",
                "-C",
                str(repo),
                "fetch",
                "--quiet",
                "--no-tags",
                str(fetch_from),
            ]
            if fetch_refspec:
                fetch_cmd.append(str(fetch_refspec))
            subprocess.run(fetch_cmd, check=True, capture_output=True, text=True)

        resolved_old = old_sha
        if not resolved_old:
            resolved_old = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", ref_name],
                text=True,
            ).strip()
        resolved_new = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", f"{new_sha}^{{commit}}"],
            text=True,
        ).strip()
        token_slug = _token_to_slug(acquired_token)
        stash_sha = ""
        effective_stash_msg = ""
        if stash_dirty:
            dirty_status = subprocess.check_output(
                ["git", "-C", str(repo), "status", "--porcelain", "-uno"],
                text=True,
            ).strip()
            if dirty_status:
                effective_stash_msg = (
                    str(stash_message).strip()
                    if stash_message
                    else f"fenced-git-ref-update-{int(now * 1000)}-{token_slug}"
                )
                stash_sha = subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "stash",
                        "create",
                        effective_stash_msg,
                    ],
                    text=True,
                ).strip()

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
            stash_sha=stash_sha,
            stash_message=effective_stash_msg,
        )
        coord._write_journal_locked(journal_entry)

        if resolved_old != resolved_new or stash_sha:
            if resolved_old != resolved_new and ref_name and ref_name != "HEAD":
                cmd = [
                    "git",
                    "-C",
                    str(repo),
                    "update-ref",
                    ref_name,
                    resolved_new,
                    resolved_old,
                ]
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            _reconcile_git_worktree_and_stash(
                repo,
                resolved_new,
                reset_mode=reset_mode,
                stash_sha=stash_sha,
                stash_message=effective_stash_msg,
                refresh_index=False,
                error_context=f"after fast-forward to {resolved_new}",
            )

        if refresh_index:
            diff_out = subprocess.check_output(
                ["git", "-C", str(repo), "diff", "--name-only", "HEAD", "--", "."],
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
                        str(repo),
                        "add",
                        "-u",
                        "--",
                        ".",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

        journal_entry = replace(journal_entry, state="PUBLISHED_LEDGER_PENDING")
        coord._write_journal_locked(journal_entry)
        record.last_heartbeat_epoch = now
        record.expires_at_epoch = min(
            now + DEFAULT_HEARTBEAT_WINDOW_S,
            hard_ceiling,
        )
        record.last_committed_digest = resolved_new
        coord._write_ledger_locked(ledger)
        coord._clear_journal_locked(
            journal_entry.tx_id, final_state="COMMITTED", entry=journal_entry
        )
        return resolved_new
