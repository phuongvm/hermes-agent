"""`hermes lease` argparse surface and CLI dispatcher for WorkspaceLeaseCoordinator.

Kept separate from `hermes_cli.workspace_lease` (the coordinator core) so the core
module stays under the AGENTS.md 2,000-line ratchet. Public entry points are
re-exported from `hermes_cli.workspace_lease` for backward compatibility.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    _repo_root = str(Path(__file__).resolve().parents[1])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

from hermes_cli.workspace_lease import WorkspaceLeaseCoordinator
from hermes_cli.workspace_lease_types import (
    EXIT_ERROR,
    EXIT_OK,
    MAX_LEASE_TTL_S,
    VALID_MODES,
    CorruptPersistentStateError,
    LeasePreemptedError,
    _extract_scope_from_token,
    normalize_scope,
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
    p_git.add_argument("--token", "--fencing-token", dest="token", required=True)
    p_git.add_argument("--repo-root", required=True)
    p_git.add_argument("--ref", default="refs/heads/main")
    p_git.add_argument("--new-sha", required=True)
    p_git.add_argument("--old-sha", default=None)
    p_git.add_argument("--reset-mode", choices=("hard", "mixed"), default="hard")
    p_git.add_argument("--fetch-from", default=None)
    p_git.add_argument("--fetch-refspec", default=None)
    p_git.add_argument("--refresh-index", action="store_true", default=False)
    p_git.add_argument("--stash-dirty", action="store_true", default=False)
    p_git.add_argument("--stash-message", default=None)

    p_hb = sub.add_parser("heartbeat", help="Renew 30s lease heartbeat")
    _add_common_lease_Flags(p_hb, is_subparser=True)
    p_hb.add_argument("--scope", required=True)
    p_hb.add_argument("--token", "--fencing-token", dest="token", required=True)

    p_rel = sub.add_parser("release", help="Release held workspace lease(s)")
    _add_common_lease_Flags(p_rel, is_subparser=True)
    p_rel.add_argument(
        "--scope",
        "--scopes",
        dest="scopes",
        action="append",
        required=True,
    )
    p_rel.add_argument("--token", "--fencing-token", dest="token", default="")
    p_rel.add_argument("--tokens-json", default="")
    p_rel.add_argument("--status", default="APPLIED")
    p_rel.add_argument("--holder-id", dest="holder_id", default=None)
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
                stash_dirty=bool(getattr(args, "stash_dirty", False)),
                stash_message=getattr(args, "stash_message", None),
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
            tokens_arg: Mapping[str, Any] | str
            if getattr(args, "tokens_json", ""):
                tokens_arg = json.loads(args.tokens_json)
            else:
                tokens_arg = str(getattr(args, "token", ""))
            report = coord.release_workspace_lease(
                scopes=raw_scopes,
                fencing_tokens=tokens_arg,
                final_status=str(getattr(args, "status", "APPLIED")),
                holder_id=getattr(args, "holder_id", None),
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
    except CorruptPersistentStateError as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "reason_code": exc.reason_code,
                    "exit_code": exc.exit_code,
                    "path": exc.path,
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
