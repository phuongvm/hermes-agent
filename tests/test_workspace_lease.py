from __future__ import annotations

import json
import signal
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from hermes_cli.workspace_lease import (
    CommitJournalRecord,
    LeaseExecutionReport,
    LeasePreemptedError,
    LeaseRecord,
    WorkspaceLeaseCoordinator,
    atomic_fenced_commit,
    atomic_fenced_git_ref_update,
    main as lease_cli_main,
    normalize_scope,
    recover_interrupted_commit,
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


def test_cutover_and_watchdog_scripts_enforce_two_phase_staging_and_exit_75_deferral() -> None:
    workspaces_root = Path(__file__).resolve().parents[3]
    cutover_scripts = [
        workspaces_root / "_config" / "agent4070" / "hermes" / "services" / "apply-live-crew-cutover.ps1",
        workspaces_root / "_config" / "agent4070" / "hermes" / "services" / "apply-live-nerve-cutover.ps1",
        workspaces_root / "_config" / "agent4070" / "hermes" / "services" / "apply-live-hermes-cutover.ps1",
    ]
    for script_path in cutover_scripts:
        text = script_path.read_text(encoding="utf-8")
        assert ".tmp." in text, f"{script_path.name} missing 2-phase .tmp.<fencing_token> staging"
        assert "Invoke-FencedCommit" in text, f"{script_path.name} missing Invoke-FencedCommit"
        assert "exit 75" in text, f"{script_path.name} missing exit 75 contention deferral"
        assert "Copy-Item" not in text, f"{script_path.name} still contains direct Copy-Item"
        assert "Move-Item" not in text, f"{script_path.name} still contains direct Move-Item"

    watchdog_scripts = [
        workspaces_root / ".agents" / "skills" / "crew-upstream-sync" / "scripts" / "daily-sync-watchdog.ps1",
        workspaces_root / ".agents" / "skills" / "nerve-upstream-sync" / "scripts" / "daily-sync-watchdog.ps1",
    ]
    for wd_path in watchdog_scripts:
        text = wd_path.read_text(encoding="utf-8")
        assert "cron_cutover_ledger.jsonl" in text, f"{wd_path} missing cron_cutover_ledger.jsonl"
        assert "Write-DeferredCronLedger" in text, f"{wd_path} missing Write-DeferredCronLedger"
        assert "-eq 75" in text and "exit 0" in text, f"{wd_path} missing exit 75 -> exit 0 translation"

