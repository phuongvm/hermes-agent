from __future__ import annotations

import json
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hermes_cli.workspace_lease import (
    MAX_LEASE_TTL_S,
    CommitJournalRecord,
    LeaseExecutionReport,
    LeasePreemptedError,
    LeaseRecord,
    WorkspaceLeaseCoordinator,
    atomic_fenced_commit,
    atomic_fenced_delete,
    atomic_fenced_git_ref_update,
    main as lease_cli_main,
    normalize_scope,
    recover_interrupted_commit,
    release_workspace_lease,
    scopes_overlap,
)


def test_scope_taxonomy_normalization_and_hierarchical_overlap() -> None:
    assert normalize_scope("  PROFILE:coder ") == "profile:coder"
    assert normalize_scope("repo:oss\\crew/") == "repo:oss/crew"
    assert normalize_scope("catalog:skills") == "catalog:skills"

    assert scopes_overlap("profile:coder", "profile:*") is True
    assert scopes_overlap("profile:*", "profile:default") is True
    assert scopes_overlap("profile:coder", "profile:reviewer") is False
    assert scopes_overlap("repo:oss/crew", "repo:*") is True
    assert scopes_overlap("repo:oss/crew", "repo:oss/hermes-nerve") is False
    assert scopes_overlap("repo:oss/crew", "profile:coder") is False
    assert scopes_overlap("catalog:skills", "catalog:skills") is True
    assert scopes_overlap("catalog:skills", "catalog:pending_skills") is False


def test_lease_state_machine_lifecycle_and_monotonic_fencing_token(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 100_000_000,
        clock=lambda: 1762732800.0,
    )

    r1 = coord.acquire_workspace_lease(
        scopes=("repo:oss/crew", "profile:*"),
        mode="exclusive_cutover",
        holder="crew-cutover-1",
        holder_pid=1234,
        process_start_time_ns=100_000_000,
    )
    assert isinstance(r1, LeaseExecutionReport)
    assert r1.status == "ACQUIRED"
    assert r1.exit_code == 0
    token_repo_1 = r1.fencing_tokens["repo:oss/crew"]
    token_prof_1 = r1.fencing_tokens["profile:*"]
    assert token_repo_1 == "repo:oss/crew:1762732800:1"
    assert token_prof_1 == "profile:*:1762732800:1"

    ledger_data = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    rec_dict = ledger_data["active_records"]["repo:oss/crew"]
    assert rec_dict["state"] == "ACQUIRED"
    assert rec_dict["generation"] == 1

    rel = coord.release_workspace_lease(
        scopes=("repo:oss/crew", "profile:*"),
        fencing_tokens=r1.fencing_tokens,
        final_status="APPLIED",
    )
    assert rel.status == "APPLIED"
    assert rel.exit_code == 0
    ledger_after = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    assert "repo:oss/crew" not in ledger_after["active_records"]
    assert ledger_after["scope_states"]["repo:oss/crew"] == "UNLOCKED"

    r2 = coord.acquire_workspace_lease(
        scopes=("repo:oss/crew",),
        mode="exclusive_cutover",
        holder="crew-cutover-2",
        holder_pid=1234,
        process_start_time_ns=100_000_000,
    )
    assert r2.status == "ACQUIRED"
    assert r2.fencing_tokens["repo:oss/crew"] == "repo:oss/crew:1762732800:2"


def test_concurrent_shared_session_defers_exclusive_cutover_exit_75(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 222_000_000,
        clock=lambda: 1762732800.0,
    )

    s1 = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="shared_session",
        holder="session-coder-1",
        holder_pid=2001,
        process_start_time_ns=222_000_000,
    )
    assert s1.status == "ACQUIRED"

    s2 = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="shared_session",
        holder="session-coder-2",
        holder_pid=2002,
        process_start_time_ns=222_000_000,
    )
    assert s2.status == "ACQUIRED"

    deferred = coord.acquire_workspace_lease(
        scopes=("profile:*", "repo:oss/crew"),
        mode="exclusive_cutover",
        holder="daily-sync-watchdog",
        holder_pid=3001,
        process_start_time_ns=222_000_000,
    )
    assert deferred.status == "DEFERRED"
    assert deferred.exit_code == 75
    assert deferred.reason_code == "DEFERRED_LEASE_HELD"

    # Also verify CLI exit 75 and active state.db / dirty worktree contention
    state_db = tmp_path / "state.db"
    conn = sqlite3.connect(state_db)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT, updated_at REAL)"
    )
    conn.execute(
        "INSERT INTO sessions (id, status, updated_at) VALUES ('sess_active', 'running', ?)",
        (1762732790.0,),
    )
    conn.commit()
    conn.close()

    coord_db = WorkspaceLeaseCoordinator(
        lease_dir=tmp_path / "leases_db",
        state_db_path=state_db,
        clock=lambda: 1762732800.0,
    )
    def_db = coord_db.acquire_workspace_lease(
        scopes=("repo:oss/crew",),
        mode="exclusive_cutover",
        holder="cron-crew",
    )
    assert def_db.status == "DEFERRED"
    assert def_db.exit_code == 75
    assert def_db.reason_code == "DEFERRED_ACTIVE_SESSION"


def test_pid_recycling_checks_process_start_time_ns(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    now = [1762732800.0]
    proc_table = {4444: 500_000_000}

    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: proc_table.get(pid),
        clock=lambda: now[0],
    )

    h1 = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="exclusive_cutover",
        holder="holder-1",
        holder_pid=4444,
        process_start_time_ns=500_000_000,
    )
    assert h1.status == "ACQUIRED"
    assert h1.fencing_tokens["profile:coder"] == "profile:coder:1762732800:1"

    # Simulate OS recycling PID 4444 for a brand-new process with different start_time_ns
    # even before the 90s heartbeat window expires.
    proc_table[4444] = 999_999_999
    now[0] += 10.0

    h2 = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="exclusive_cutover",
        holder="holder-2",
        holder_pid=5555,
        process_start_time_ns=888_000_000,
    )
    assert h2.status == "ACQUIRED"
    assert h2.fencing_tokens["profile:coder"] == "profile:coder:1762732800:2"

    ledger = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    assert ledger["history"][-1]["state"] == "PREEMPTED"
    assert ledger["history"][-1]["fencing_token"] == "profile:coder:1762732800:1"


def test_heartbeat_renewal_extends_rolling_window_up_to_max_ttl(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    now = [1762732800.0]
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 111_000_000,
        clock=lambda: now[0],
    )
    acq = coord.acquire_workspace_lease(
        scopes=("catalog:skills",),
        mode="exclusive_cutover",
        holder="skill-sync",
        ttl_s=1800.0,
        holder_pid=1010,
        process_start_time_ns=111_000_000,
    )
    token = acq.fencing_tokens["catalog:skills"]

    # Advance 30s and renew heartbeat
    now[0] += 30.0
    updated: LeaseRecord = coord.renew_heartbeat("catalog:skills", token)
    assert updated.last_heartbeat_epoch == 1762732830.0
    assert updated.expires_at_epoch == 1762732830.0 + 90.0

    # Advance in 30s heartbeat ticks near 1800s max TTL ceiling: renewal must cap at acquired_at_epoch + ttl_s
    updated_cap = updated
    while now[0] + 30.0 <= 1762732800.0 + 1740.0:
        now[0] += 30.0
        updated_cap = coord.renew_heartbeat("catalog:skills", token)
    assert updated_cap.expires_at_epoch == 1762732800.0 + 1800.0

    # Advancing past the 1800s max TTL ceiling causes renew_heartbeat to fail closed
    now[0] = 1762732800.0 + 1801.0
    with pytest.raises(LeasePreemptedError):
        coord.renew_heartbeat("catalog:skills", token)


def test_preempted_holder_staged_write_aborted_without_side_effects(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    now = [1762732800.0]
    proc_table = {1001: 100_000, 1002: 200_000}

    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: proc_table.get(pid),
        clock=lambda: now[0],
    )

    # Holder 1 acquires lease
    h1 = coord.acquire_workspace_lease(
        scopes=("profile:*",),
        mode="exclusive_cutover",
        holder="holder-1",
        holder_pid=1001,
        process_start_time_ns=100_000,
    )
    token_1 = h1.fencing_tokens["profile:*"]
    slug_1 = token_1.replace(":", "_").replace("*", "star")

    target_file = tmp_path / "skill-review-evidence.md"
    target_file.write_text("initial-baseline", encoding="utf-8")

    # Holder 1 stages its write during Phase A
    staged_1 = tmp_path / f"skill-review-evidence.md.tmp.{slug_1}"
    staged_1.write_text("stale-holder-1-payload", encoding="utf-8")

    # Holder 1 stalls past the 90s heartbeat expiry window
    now[0] += 95.0

    # Holder 2 preempts the expired lease and commits its own valid update
    h2 = coord.acquire_workspace_lease(
        scopes=("profile:*",),
        mode="exclusive_cutover",
        holder="holder-2",
        holder_pid=1002,
        process_start_time_ns=200_000,
    )
    assert h2.status == "ACQUIRED"
    token_2 = h2.fencing_tokens["profile:*"]
    assert token_2 == "profile:*:1762732800:2"

    slug_2 = token_2.replace(":", "_").replace("*", "star")
    staged_2 = tmp_path / f"skill-review-evidence.md.tmp.{slug_2}"
    staged_2.write_text("authoritative-holder-2-payload", encoding="utf-8")
    coord.atomic_fenced_commit("profile:*", token_2, staged_2, target_file)
    assert target_file.read_text(encoding="utf-8") == "authoritative-holder-2-payload"

    # Holder 1 wakes up and attempts Phase B/C commit with stale token_1
    with pytest.raises(LeasePreemptedError) as exc_info:
        coord.atomic_fenced_commit("profile:*", token_1, staged_1, target_file)

    assert exc_info.value.reason_code == "STALE_FENCING_TOKEN"
    assert exc_info.value.exit_code == 78
    # Staged temp file must be purged and target_file must remain 100% untouched
    assert not staged_1.exists()
    assert target_file.read_text(encoding="utf-8") == "authoritative-holder-2-payload"

    # Also verify CLI `hermes lease commit` exits with code 78 on stale token
    staged_1_cli = tmp_path / f"skill-review-evidence.md.tmp.{slug_1}_cli"
    staged_1_cli.write_text("stale-cli-payload", encoding="utf-8")
    rc = lease_cli_main(
        [
            "--lease-dir",
            str(lease_dir),
            "commit",
            "--scope",
            "profile:*",
            "--token",
            token_1,
            "--staged",
            str(staged_1_cli),
            "--target",
            str(target_file),
        ]
    )
    assert rc == 78
    assert not staged_1_cli.exists()
    assert target_file.read_text(encoding="utf-8") == "authoritative-holder-2-payload"


def test_commit_journal_rolls_forward_ledger_after_post_publish_crash(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 333_000,
        clock=lambda: 1762732800.0,
    )
    acq = coord.acquire_workspace_lease(
        scopes=("catalog:evidence_log",),
        mode="exclusive_cutover",
        holder="evidence-writer",
        holder_pid=3000,
        process_start_time_ns=333_000,
    )
    token = acq.fencing_tokens["catalog:evidence_log"]
    slug = token.replace(":", "_")

    target_path = tmp_path / "evidence.json"
    target_path.write_text('{"v": 1}\n', encoding="utf-8")
    staged_path = tmp_path / f"evidence.json.tmp.{slug}"
    staged_path.write_text('{"v": 2}\n', encoding="utf-8")

    # Simulate crash right after target publication (PUBLISHED_LEDGER_PENDING) before ledger update
    expected_digest = coord._compute_path_digest(staged_path)
    backup_path = tmp_path / f"evidence.json.prev.{slug}"
    backup_path.write_text('{"v": 1}\n', encoding="utf-8")
    staged_path.replace(target_path)

    journal_entry = CommitJournalRecord(
        tx_id=f"tx_1762732800000_{slug}",
        state="PUBLISHED_LEDGER_PENDING",
        scope="catalog:evidence_log",
        fencing_token=token,
        target_path=str(target_path),
        backup_path=str(backup_path),
        staged_path=str(staged_path),
        staged_digest=expected_digest,
        started_at_epoch=1762732800.0,
    )
    (lease_dir / "workspace_lease.journal.json").write_text(
        json.dumps({"active_tx": journal_entry.__dict__}, indent=2),
        encoding="utf-8",
    )

    recovered = coord.recover_interrupted_commit()
    assert len(recovered) == 1
    assert recovered[0].state == "COMMITTED"
    assert not backup_path.exists()
    assert target_path.read_text(encoding="utf-8") == '{"v": 2}\n'

    ledger = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    assert ledger["active_records"]["catalog:evidence_log"]["last_committed_digest"] == expected_digest


def test_directory_swap_mid_crash_restores_from_backup_via_journal(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 444_000,
        clock=lambda: 1762732800.0,
    )
    acq = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="exclusive_cutover",
        holder="crew-cutover",
        holder_pid=4000,
        process_start_time_ns=444_000,
    )
    token = acq.fencing_tokens["profile:coder"]
    slug = token.replace(":", "_")

    target_dir = tmp_path / "plugins_crew"
    backup_dir = tmp_path / f"plugins_crew.prev.{slug}"
    staged_dir = tmp_path / f"plugins_crew.tmp.{slug}"

    # Simulate mid-directory-swap crash: target_dir was moved to backup_dir,
    # but process crashed before staged_dir was moved to target_dir!
    backup_dir.mkdir(parents=True)
    (backup_dir / "__init__.py").write_text(" VERSION = 'old'\n", encoding="utf-8")
    staged_dir.mkdir(parents=True)
    (staged_dir / "__init__.py").write_text(" VERSION = 'new'\n", encoding="utf-8")
    staged_digest = coord._compute_path_digest(staged_dir)

    journal_entry = CommitJournalRecord(
        tx_id=f"tx_1762732801000_{slug}",
        state="PUBLISH_INTENT",
        scope="profile:coder",
        fencing_token=token,
        target_path=str(target_dir),
        backup_path=str(backup_dir),
        staged_path=str(staged_dir),
        staged_digest=staged_digest,
        started_at_epoch=1762732801.0,
    )
    (lease_dir / "workspace_lease.journal.json").write_text(
        json.dumps({"active_tx": journal_entry.__dict__}, indent=2),
        encoding="utf-8",
    )

    recovered = coord.recover_interrupted_commit()
    assert len(recovered) == 1
    assert recovered[0].state == "ROLLED_BACK"
    assert target_dir.exists()
    assert (target_dir / "__init__.py").read_text(encoding="utf-8") == " VERSION = 'old'\n"
    assert not backup_dir.exists()
    assert not staged_dir.exists()


def test_manual_wait_timeout_and_sigint_cleanup(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    now = [1762732800.0]
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 777_000,
        clock=lambda: now[0],
    )

    # Hold an active lease
    h1 = coord.acquire_workspace_lease(
        scopes=("repo:oss/hermes-agent",),
        mode="shared_session",
        holder="active-worker",
        holder_pid=7000,
        process_start_time_ns=777_000,
    )
    assert h1.status == "ACQUIRED"

    # 1. Bounded wait timeout returns DEFERRED (exit 75)
    res_timeout = coord.acquire_workspace_lease(
        scopes=("repo:oss/hermes-agent",),
        mode="exclusive_cutover",
        holder="manual-operator",
        wait_seconds=0.15,
        poll_interval_s=0.05,
        holder_pid=7001,
        process_start_time_ns=777_000,
    )
    assert res_timeout.status == "DEFERRED"
    assert res_timeout.exit_code == 75

    # 2. SIGINT / stop_event during wait aborts polling within < 1.0s and leaves lock unlocked
    stop_event = threading.Event()
    timer = threading.Timer(0.05, stop_event.set)
    timer.start()
    try:
        res_sigint = coord.acquire_workspace_lease(
            scopes=("repo:oss/hermes-agent",),
            mode="exclusive_cutover",
            holder="manual-operator-sigint",
            wait_seconds=60.0,
            poll_interval_s=0.5,
            holder_pid=7002,
            process_start_time_ns=777_000,
            stop_event=stop_event,
        )
    finally:
        timer.cancel()

    assert res_sigint.status == "DEFERRED"
    assert res_sigint.exit_code == 75
    assert res_sigint.reason_code == "INTERRUPTED_SIGINT"

    # Verify workspace_lease.lock is unlocked and usable immediately after SIGINT abort
    with coord._acquire_kernel_mutex(timeout_s=0.5):
        pass


def test_atomic_fenced_git_ref_update_fast_forwards_and_journals(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)

    f1 = repo / "app.py"
    f1.write_text("v = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "c1"], check=True, capture_output=True)
    sha1 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Create a second commit object on a temp branch, then reset main back to sha1
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "incoming"], check=True, capture_output=True)
    f1.write_text("v = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "c2"], check=True, capture_output=True)
    sha2 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True, capture_output=True)

    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 555_000,
        clock=lambda: 1762732800.0,
    )
    acq = coord.acquire_workspace_lease(
        scopes=("repo:oss/crew",),
        mode="exclusive_cutover",
        holder="crew-cutover",
        holder_pid=5000,
        process_start_time_ns=555_000,
    )
    token = acq.fencing_tokens["repo:oss/crew"]

    updated_sha = coord.atomic_fenced_git_ref_update(
        scope="repo:oss/crew",
        acquired_token=token,
        repo_root=repo,
        ref_name="refs/heads/main",
        new_sha=sha2,
        old_sha=sha1,
    )
    assert updated_sha == sha2
    head_now = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_now == sha2
    assert f1.read_text(encoding="utf-8") == "v = 2\n"


def test_commit_journal_aborts_and_purges_staged_on_pre_swap_crash(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 666_000,
        clock=lambda: 1762732800.0,
    )
    acq = coord.acquire_workspace_lease(
        scopes=("profile:default",),
        mode="exclusive_cutover",
        holder="pre-swap-holder",
        holder_pid=6000,
        process_start_time_ns=666_000,
    )
    token = acq.fencing_tokens["profile:default"]
    slug = token.replace(":", "_")

    target_file = tmp_path / "config.yaml"
    target_file.write_text("model: baseline\n", encoding="utf-8")
    staged_file = tmp_path / f"config.yaml.tmp.{slug}"
    staged_file.write_text("model: uncommitted\n", encoding="utf-8")
    staged_digest = coord._compute_path_digest(staged_file)

    journal_entry = CommitJournalRecord(
        tx_id=f"tx_1762732802000_{slug}",
        state="PUBLISH_INTENT",
        scope="profile:default",
        fencing_token=token,
        target_path=str(target_file),
        backup_path=str(tmp_path / f"config.yaml.prev.{slug}"),
        staged_path=str(staged_file),
        staged_digest=staged_digest,
        started_at_epoch=1762732802.0,
    )
    (lease_dir / "workspace_lease.journal.json").write_text(
        json.dumps({"active_tx": journal_entry.to_dict()}, indent=2),
        encoding="utf-8",
    )

    recovered = recover_interrupted_commit(lease_dir=lease_dir)
    assert len(recovered) == 1
    assert recovered[0].state == "ABORTED"
    assert not staged_file.exists()
    assert target_file.read_text(encoding="utf-8") == "model: baseline\n"


def test_cutover_and_watchdog_scripts_enforce_two_phase_staging_and_exit_75_deferral(
    tmp_path: Path,
) -> None:
    workspaces_root = Path(__file__).resolve().parents[3]
    hermes_cutover = (
        workspaces_root
        / "_config"
        / "agent4070"
        / "hermes"
        / "services"
        / "apply-live-hermes-cutover.ps1"
    )
    lease_script = Path(__file__).resolve().parents[1] / "hermes_cli" / "workspace_lease.py"

    # Behavioral test (A-04 / A-01R): execute the real PowerShell wrapper functions from
    # apply-live-hermes-cutover.ps1 against disposable git/file targets and lease store.
    lease_dir = tmp_path / "leases"
    now = [time.time() - 100.0]
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 100,
        clock=lambda: now[0],
    )
    h1 = coord.acquire_workspace_lease(
        scopes=("repo:oss/hermes-agent",),
        mode="exclusive_cutover",
        holder="cutover-1",
        holder_pid=101,
        process_start_time_ns=100,
    )
    token_1 = h1.fencing_tokens["repo:oss/hermes-agent"]

    repo = tmp_path / "disposable_hermes_repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    tracked_a = repo / "core.py"
    tracked_b = repo / "local_cfg.py"
    tracked_a.write_text("v = 1\n", encoding="utf-8")
    tracked_b.write_text("local = 'baseline'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "c1"], check=True, capture_output=True)
    sha1 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "incoming"], check=True, capture_output=True)
    tracked_a.write_text("v = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "c2"], check=True, capture_output=True)
    sha2 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True, capture_output=True)

    # Dirty tracked modification on local_cfg.py before preempted cutover attempt
    tracked_b.write_text("local = 'dirty-operator-edit'\n", encoding="utf-8")

    # Preempt Holder 1 -> Holder 2 acquires lease at current wall-clock time
    now[0] = time.time()
    h2 = coord.acquire_workspace_lease(
        scopes=("repo:oss/hermes-agent",),
        mode="exclusive_cutover",
        holder="cutover-2",
        holder_pid=202,
        process_start_time_ns=100,
    )
    token_2 = h2.fencing_tokens["repo:oss/hermes-agent"]

    ps_prefix = (
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{hermes_cutover.as_posix()}', [ref]$null, [ref]$null); "
        "$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-FencedGitRefUpdate' }, $true) | Select-Object -First 1; "
        "Invoke-Expression $fn.Extent.Text; "
        f"$LeasePythonExe = '{Path(sys.executable).as_posix()}'; "
        f"$LeaseScript = '{lease_script.as_posix()}'; "
        f"$LeaseDir = '{lease_dir.as_posix()}'; "
    )

    # Stale Holder 1 invokes Invoke-FencedGitRefUpdate -StashDirty: MUST exit 78 with 0 repo/stash mutations
    ps_stale = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-Command",
            ps_prefix
            + f"Invoke-FencedGitRefUpdate -Scope 'repo:oss/hermes-agent' -Token '{token_1}' -RepoPath '{repo.as_posix()}' -RefName 'refs/heads/main' -NewSha '{sha2}' -OldSha '{sha1}' -StashDirty -StashMessage 'stale-cutover-stash'",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ps_stale.returncode == 78
    head_after_stale = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_after_stale == sha1
    assert tracked_a.read_text(encoding="utf-8") == "v = 1\n"
    assert tracked_b.read_text(encoding="utf-8") == "local = 'dirty-operator-edit'\n"

    # Active Holder 2 runs Invoke-FencedGitRefUpdate from apply-live-hermes-cutover.ps1 (with -StashDirty):
    # advances HEAD to sha2 AND preserves dirty tracked file
    ps_res = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-Command",
            ps_prefix
            + f"Invoke-FencedGitRefUpdate -Scope 'repo:oss/hermes-agent' -Token '{token_2}' -RepoPath '{repo.as_posix()}' -RefName 'refs/heads/main' -NewSha '{sha2}' -OldSha '{sha1}' -StashDirty -StashMessage 'active-cutover-stash'",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ps_res.returncode == 0, ps_res.stderr or ps_res.stdout
    head_after_ok = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_after_ok == sha2
    assert tracked_a.read_text(encoding="utf-8") == "v = 2\n"
    assert tracked_b.read_text(encoding="utf-8") == "local = 'dirty-operator-edit'\n"


def test_shared_session_partial_release_retains_sibling_holders_s01(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 1,
        clock=lambda: 1000.0,
    )

    s1 = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="shared_session",
        holder="A",
        holder_pid=11,
        process_start_time_ns=1,
    )
    s2 = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="shared_session",
        holder="B",
        holder_pid=22,
        process_start_time_ns=1,
    )
    assert s1.status == "ACQUIRED"
    assert s2.status == "ACQUIRED"
    assert s1.holder_id == "11:1:A"
    assert s2.holder_id == "22:1:B"

    # 1. Second-holder-first release (Holder B / PID 22 first) after full JSON round-trip (S-01R repro)
    round_tripped_b_tokens = json.loads(json.dumps(s2.fencing_tokens))
    rel_22 = coord.release_workspace_lease(
        scopes=("profile:coder",),
        fencing_tokens=round_tripped_b_tokens,
    )
    assert rel_22.exit_code == 0
    assert rel_22.reason_code == "LEASE_RELEASED"
    assert rel_22.scopes == ("profile:coder",)

    record_after_b = coord.get_status()["ledger"]["active_records"]["profile:coder"]
    assert record_after_b["state"] == "ACQUIRED"
    assert [int(h["pid"]) for h in record_after_b["shared_holders"]] == [11]

    # 2. Duplicate release of already-released Holder B must be a safe no-op and MUST NOT remove Holder A (PID 11)
    rel_22_dup = coord.release_workspace_lease(
        scopes=("profile:coder",),
        fencing_tokens=round_tripped_b_tokens,
    )
    assert rel_22_dup.status == "NOOP"
    assert rel_22_dup.reason_code == "HOLDER_NOT_FOUND"
    assert rel_22_dup.scopes == ()
    record_after_dup = coord.get_status()["ledger"]["active_records"]["profile:coder"]
    assert [int(h["pid"]) for h in record_after_dup["shared_holders"]] == [11]

    # 3. Unknown holder release (explicit pid=999, unknown holder_id, or CLI --pid 999) MUST NOT remove Holder A (PID 11)
    rel_unknown_pid = coord.release_workspace_lease(
        scopes=("profile:coder",),
        fencing_tokens=json.loads(json.dumps(s1.fencing_tokens)),
        pid=999,
    )
    assert rel_unknown_pid.status == "NOOP"
    assert rel_unknown_pid.reason_code == "HOLDER_NOT_FOUND"
    assert rel_unknown_pid.scopes == ()

    rel_unknown_id = coord.release_workspace_lease(
        scopes=("profile:coder",),
        fencing_tokens=json.loads(json.dumps(s1.fencing_tokens)),
        holder_id="999:1:ghost",
    )
    assert rel_unknown_id.status == "NOOP"
    assert rel_unknown_id.reason_code == "HOLDER_NOT_FOUND"

    rc_cli_unknown = lease_cli_main(
        [
            "--lease-dir",
            str(lease_dir),
            "release",
            "--scope",
            "profile:coder",
            "--tokens-json",
            json.dumps(s1.fencing_tokens),
            "--pid",
            "999",
        ]
    )
    assert rc_cli_unknown == 0

    # 4. Bare scope token without holder identity on shared_session MUST fail closed / no-op without popping PID 11
    base_only_token = s1.fencing_tokens["profile:coder"].split("@", 1)[0]
    rel_bare = coord.release_workspace_lease(
        scopes=("profile:coder",),
        fencing_tokens={"profile:coder": base_only_token},
    )
    assert rel_bare.status == "NOOP"
    assert rel_bare.reason_code == "SHARED_HOLDER_ID_REQUIRED"
    assert rel_bare.scopes == ()

    record_still_11 = coord.get_status()["ledger"]["active_records"]["profile:coder"]
    assert [int(h["pid"]) for h in record_still_11["shared_holders"]] == [11]

    # 5. Exclusive contender must still receive DEFERRED (exit 75) while PID 11 holds the shared lease
    contender = coord.acquire_workspace_lease(
        scopes=("profile:*",),
        mode="exclusive_cutover",
        holder="exclusive-contender",
        holder_pid=99,
        process_start_time_ns=1,
    )
    assert contender.status == "DEFERRED"
    assert contender.exit_code == 75
    assert contender.reason_code == "DEFERRED_LEASE_HELD"

    # 6. Now release PID 11 via JSON-serialized token map; scope must transition to UNLOCKED
    rel_11 = coord.release_workspace_lease(
        scopes=("profile:coder",),
        fencing_tokens=json.loads(json.dumps(s1.fencing_tokens)),
    )
    assert rel_11.exit_code == 0
    assert rel_11.reason_code == "LEASE_RELEASED"
    ledger_end = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    assert "profile:coder" not in ledger_end["active_records"]
    assert ledger_end["scope_states"]["profile:coder"] == "UNLOCKED"

    contender_after = coord.acquire_workspace_lease(
        scopes=("profile:*",),
        mode="exclusive_cutover",
        holder="exclusive-contender",
        holder_pid=99,
        process_start_time_ns=1,
    )
    assert contender_after.status == "ACQUIRED"
    assert contender_after.exit_code == 0


def test_max_lease_ttl_ceiling_clamped_on_acquire_and_heartbeat_s02(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    now = [1000.0]
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 1,
        clock=lambda: now[0],
    )

    assert MAX_LEASE_TTL_S == 1800.0

    # Acquire with ttl_s=3600; verify clamped to 1800.0 and max_expires_at == 2800.0
    acq = coord.acquire_workspace_lease(
        scopes=("profile:coder",),
        mode="exclusive_cutover",
        holder="long-runner",
        ttl_s=3600.0,
        holder_pid=42,
        process_start_time_ns=1,
    )
    assert acq.status == "ACQUIRED"
    token = acq.fencing_tokens["profile:coder"]

    ledger_data = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    rec_data = ledger_data["active_records"]["profile:coder"]
    assert rec_data["ttl_s"] == 1800.0
    assert rec_data["max_expires_at"] == 1000.0 + 1800.0

    # Renew every 30s up to 2800.0; expiry must never exceed acquired_at + 1800.0 (2800.0)
    last_rec = None
    while now[0] + 30.0 <= 2800.0:
        now[0] += 30.0
        last_rec = coord.renew_lease_heartbeat("profile:coder", token)
        assert last_rec.ttl_s == 1800.0
        assert last_rec.max_expires_at == 2800.0
        assert last_rec.expires_at_epoch <= 2800.0

    assert last_rec is not None
    assert last_rec.expires_at_epoch == 2800.0

    # Attempting renewal past 1800s from initial acquisition (at t=2830.0) must fail closed
    now[0] = 2830.0
    with pytest.raises(LeasePreemptedError):
        coord.renew_lease_heartbeat("profile:coder", token)

    # Even if persisted JSON on disk is tampered with ttl_s=99999, loading clamps to 1800.0
    tampered = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    tampered["active_records"]["profile:coder"]["ttl_s"] = 99999.0
    tampered["active_records"]["profile:coder"]["max_expires_at"] = 999999.0
    (lease_dir / "workspace_leases.json").write_text(json.dumps(tampered), encoding="utf-8")
    loaded_rec = LeaseRecord.from_dict(tampered["active_records"]["profile:coder"])
    assert loaded_rec.ttl_s == 1800.0
    assert loaded_rec.max_expires_at == 1000.0 + 1800.0

    # Invalid / negative / NaN TTL values are rejected; +inf is clamped to 1800.0
    with pytest.raises(ValueError):
        coord.acquire_workspace_lease(scopes=("profile:qa",), ttl_s=-10.0)
    with pytest.raises(ValueError):
        coord.acquire_workspace_lease(scopes=("profile:qa",), ttl_s=float("nan"))
    inf_acq = coord.acquire_workspace_lease(
        scopes=("profile:qa",), ttl_s=float("inf"), holder_pid=77, process_start_time_ns=1
    )
    assert inf_acq.status == "ACQUIRED"
    ledger_inf = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    assert ledger_inf["active_records"]["profile:qa"]["ttl_s"] == 1800.0


def test_git_crash_recovery_reconciles_worktree_bytes_before_committed_a02(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)

    tracked = repo / "module.py"
    tracked.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "module.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "old"], check=True, capture_output=True)
    old_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    tracked.write_text("new\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "new"], check=True, capture_output=True)
    new_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Reset back to old_sha so working tree has "old\n" while new_sha exists in object db
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", old_sha], check=True, capture_output=True)
    assert tracked.read_text(encoding="utf-8") == "old\n"

    lease_dir = tmp_path / "leases"
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 1,
        clock=lambda: 1762732800.0,
    )
    acq = coord.acquire_workspace_lease(
        scopes=("repo:oss/crew",),
        mode="exclusive_cutover",
        holder="crew-cutover",
        holder_pid=5000,
        process_start_time_ns=1,
    )
    token = acq.fencing_tokens["repo:oss/crew"]

    real_run = subprocess.run

    def crash_before_reset(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, list) and "reset" in cmd:
            raise RuntimeError("injected crash after update-ref before reset")
        return real_run(cmd, *args, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(subprocess, "run", crash_before_reset)
    with pytest.raises(RuntimeError, match="injected crash after update-ref before reset"):
        coord.atomic_fenced_git_ref_update(
            scope="repo:oss/crew",
            acquired_token=token,
            repo_root=repo,
            ref_name="refs/heads/main",
            new_sha=new_sha,
            old_sha=old_sha,
        )

    # Confirm split state right after crash: ref is new_sha, but worktree file is still "old\n"
    monkeypatch.setattr(subprocess, "run", real_run)
    head_mid = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_mid == new_sha
    assert tracked.read_text(encoding="utf-8") == "old\n"

    # Now run recover_interrupted_commit(): it MUST reconcile the worktree to "new\n" before marking COMMITTED
    recovered = coord.recover_interrupted_commit()
    assert len(recovered) == 1
    assert recovered[0].state == "COMMITTED"
    assert tracked.read_text(encoding="utf-8") == "new\n"
    ledger = json.loads((lease_dir / "workspace_leases.json").read_text(encoding="utf-8"))
    assert ledger["active_records"]["repo:oss/crew"]["last_committed_digest"] == new_sha

    # Also verify crash recovery for `stash_dirty=True` (A-01R): if crash happens after update-ref
    # and before stash apply, recovery reconciles HEAD/worktree to new commit AND applies journaled stash_sha
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", old_sha], check=True, capture_output=True)
    local_note = repo / "local_note.py"
    local_note.write_text("operator_dirty = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "local_note.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base-with-note"], check=True, capture_output=True)
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked.write_text("new_v3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "v3"], check=True, capture_output=True)
    v3_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", base_sha], check=True, capture_output=True)
    local_note.write_text("operator_dirty = 2\n", encoding="utf-8")

    def crash_before_stash_apply(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, list) and len(cmd) >= 5 and cmd[3:5] == ["stash", "apply"]:
            raise RuntimeError("injected crash before stash apply")
        return real_run(cmd, *args, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(subprocess, "run", crash_before_stash_apply)
    with pytest.raises(RuntimeError, match="injected crash before stash apply"):
        coord.atomic_fenced_git_ref_update(
            scope="repo:oss/crew",
            acquired_token=token,
            repo_root=repo,
            ref_name="refs/heads/main",
            new_sha=v3_sha,
            old_sha=base_sha,
            stash_dirty=True,
            stash_message="cutover-stash-test",
        )
    monkeypatch.setattr(subprocess, "run", real_run)
    recovered_stash = coord.recover_interrupted_commit()
    assert len(recovered_stash) == 1
    assert recovered_stash[0].state == "COMMITTED"
    assert tracked.read_text(encoding="utf-8") == "new_v3\n"
    assert local_note.read_text(encoding="utf-8") == "operator_dirty = 2\n"

    # S-04 qualification: verify `reset_mode="mixed"` recovery aligns HEAD and index to `v3_sha`
    # while intentionally preserving working-tree overlay bytes on disk.
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", base_sha], check=True, capture_output=True)
    tracked.write_text("overlay_preserved_on_disk\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", crash_before_reset)
    with pytest.raises(RuntimeError, match="injected crash after update-ref before reset"):
        coord.atomic_fenced_git_ref_update(
            scope="repo:oss/crew",
            acquired_token=token,
            repo_root=repo,
            ref_name="refs/heads/main",
            new_sha=v3_sha,
            old_sha=base_sha,
            reset_mode="mixed",
        )
    monkeypatch.setattr(subprocess, "run", real_run)
    recovered_mixed = coord.recover_interrupted_commit()
    assert len(recovered_mixed) == 1
    assert recovered_mixed[0].state == "COMMITTED"
    head_mixed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    index_blob_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ":module.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    v3_blob_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{v3_sha}:module.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_mixed == v3_sha
    assert index_blob_sha == v3_blob_sha
    assert tracked.read_text(encoding="utf-8") == "overlay_preserved_on_disk\n"


def test_atomic_fenced_delete_and_cli_preemption_and_recovery_a01(tmp_path: Path) -> None:
    lease_dir = tmp_path / "leases"
    now = [time.time()]
    coord = WorkspaceLeaseCoordinator(
        lease_dir=lease_dir,
        process_inspector=lambda pid: 100,
        clock=lambda: now[0],
    )

    h1 = coord.acquire_workspace_lease(
        scopes=("profile:*",),
        mode="exclusive_cutover",
        holder="holder-1",
        holder_pid=101,
        process_start_time_ns=100,
    )
    token_1 = h1.fencing_tokens["profile:*"]

    # 1. Valid fenced deletion with backup/quarantine copy and empty parent pruning
    root_dir = tmp_path / "profile_root"
    nested_dir = root_dir / "plugins" / "crew" / "obsolete"
    nested_dir.mkdir(parents=True)
    retired_file = nested_dir / "old_helper.py"
    retired_file.write_text("# retired\n", encoding="utf-8")
    backup_file = tmp_path / "backups" / "old_helper.py"

    atomic_fenced_delete(
        retired_file,
        token_1,
        backup_path=backup_file,
        prune_empty_parents_up_to=root_dir / "plugins" / "crew",
        lease_dir=lease_dir,
    )
    assert not retired_file.exists()
    assert not nested_dir.exists()
    assert (root_dir / "plugins" / "crew").exists()
    assert backup_file.read_text(encoding="utf-8") == "# retired\n"

    # 2. Preemption test: Holder 1 expires, Holder 2 acquires and writes successor file;
    # Holder 1's late `hermes lease delete` must exit 78 and leave Holder 2's file intact!
    now[0] += 95.0
    h2 = coord.acquire_workspace_lease(
        scopes=("profile:*",),
        mode="exclusive_cutover",
        holder="holder-2",
        holder_pid=202,
        process_start_time_ns=100,
    )
    assert h2.status == "ACQUIRED"
    token_2 = h2.fencing_tokens["profile:*"]

    successor_file = root_dir / "plugins" / "crew" / "active.py"
    successor_file.write_text("successor = True\n", encoding="utf-8")

    with pytest.raises(LeasePreemptedError) as exc_info:
        coord.atomic_fenced_delete(successor_file, token_1)
    assert exc_info.value.exit_code == 78
    assert successor_file.read_text(encoding="utf-8") == "successor = True\n"

    rc_stale = lease_cli_main(
        [
            "--lease-dir",
            str(lease_dir),
            "delete",
            "--path",
            str(successor_file),
            "--fencing-token",
            token_1,
        ]
    )
    assert rc_stale == 78
    assert successor_file.read_text(encoding="utf-8") == "successor = True\n"

    # 3. Valid CLI delete with Holder 2's active token succeeds
    rc_ok = lease_cli_main(
        [
            "--lease-dir",
            str(lease_dir),
            "delete",
            "--path",
            str(successor_file),
            "--fencing-token",
            token_2,
        ]
    )
    assert rc_ok == 0
    assert not successor_file.exists()

    # 3b. Injected deletion journal crash recovery (A-04): simulate crash in PUBLISHED_LEDGER_PENDING
    # before target removal/parent pruning completes, and verify recover_interrupted_commit finishes it.
    crash_dir = root_dir / "plugins" / "crew" / "crash_retired"
    crash_dir.mkdir(parents=True)
    crash_target = crash_dir / "dangling.py"
    crash_target.write_text("# to-be-deleted\n", encoding="utf-8")
    internal_bk = lease_dir / ".delete_backup_crash"
    internal_bk.write_text("# to-be-deleted\n", encoding="utf-8")
    caller_bk = tmp_path / "backups" / "dangling_quarantine.py"
    del_journal = CommitJournalRecord(
        tx_id="tx_delete_crash_1",
        state="PUBLISHED_LEDGER_PENDING",
        scope="profile:*",
        fencing_token=token_2,
        target_path=str(crash_target),
        backup_path=str(internal_bk),
        staged_path="",
        staged_digest="DELETED:crash_digest",
        started_at_epoch=now[0],
        kind="delete",
        caller_backup_path=str(caller_bk),
        prune_empty_parents_up_to=str(root_dir / "plugins" / "crew"),
    )
    (lease_dir / "workspace_lease.journal.json").write_text(
        json.dumps({"active_tx": del_journal.to_dict()}, indent=2),
        encoding="utf-8",
    )
    recovered_del = coord.recover_interrupted_commit()
    assert len(recovered_del) == 1
    assert recovered_del[0].state == "COMMITTED"
    assert not crash_target.exists()
    assert not crash_dir.exists()
    assert caller_bk.read_text(encoding="utf-8") == "# to-be-deleted\n"
    assert not internal_bk.exists()

    # 4. Preempted git-ref-update --reset-mode mixed (Nerve plugin alignment seam) exits 78 without mutating HEAD
    plugin_repo = tmp_path / "plugin_nerve"
    plugin_repo.mkdir()
    subprocess.run(["git", "-C", str(plugin_repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(plugin_repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(plugin_repo), "config", "user.name", "Test"], check=True)
    (plugin_repo / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(plugin_repo), "add", "__init__.py"], check=True)
    subprocess.run(["git", "-C", str(plugin_repo), "commit", "-m", "c1"], check=True, capture_output=True)
    sha_c1 = subprocess.run(
        ["git", "-C", str(plugin_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (plugin_repo / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(plugin_repo), "commit", "-am", "c2"], check=True, capture_output=True)
    sha_c2 = subprocess.run(
        ["git", "-C", str(plugin_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    rc_git_stale = lease_cli_main(
        [
            "--lease-dir",
            str(lease_dir),
            "git-ref-update",
            "--scope",
            "profile:*",
            "--token",
            token_1,
            "--repo-root",
            str(plugin_repo),
            "--ref",
            "HEAD",
            "--new-sha",
            sha_c1,
            "--old-sha",
            sha_c2,
            "--reset-mode",
            "mixed",
            "--refresh-index",
        ]
    )
    assert rc_git_stale == 78
    head_after_stale = subprocess.run(
        ["git", "-C", str(plugin_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_after_stale == sha_c2
