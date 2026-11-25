"""Unit tests for read-only audit_worktrees TTL/mtime cache and destructive bypass (B2.3)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hermes_cli import worktree_gc


def _git(args, cwd):
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
    )
    res = subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(cwd), env=env, check=False)
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res.stdout.strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home" / ".hermes"))
    (tmp_path / "home" / ".hermes").mkdir(parents=True, exist_ok=True)
    if hasattr(worktree_gc, "invalidate_worktree_audit_cache"):
        worktree_gc.invalidate_worktree_audit_cache()

    r = tmp_path / "repo"
    _git(["init", "-b", "main", str(r)], tmp_path)
    _git(["config", "core.autocrlf", "false"], r)
    (r / "README.md").write_text("init\n", encoding="utf-8", newline="\n")
    _git(["add", "."], r)
    _git(["commit", "-m", "init"], r)
    (r / ".worktrees").mkdir()
    yield r
    if hasattr(worktree_gc, "invalidate_worktree_audit_cache"):
        worktree_gc.invalidate_worktree_audit_cache()


def _add_wt(repo_path: Path, name: str) -> Path:
    wt = repo_path / ".worktrees" / name
    _git(["worktree", "add", str(wt), "-b", f"hermes/{name}"], repo_path)
    return wt


def test_repeated_audit_within_ttl_uses_cache_without_spawning_classify_subprocesses(repo, monkeypatch):
    _add_wt(repo, "wt-one")
    clock = [500.0]
    monkeypatch.setattr(worktree_gc, "_audit_monotonic", lambda: clock[0])

    classify_calls = []
    real_classify = worktree_gc._classify_tree

    def spy_classify(*args, **kwargs):
        classify_calls.append(1)
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(worktree_gc, "_classify_tree", spy_classify)

    first = worktree_gc.audit_worktrees(str(repo), with_sizes=False)
    assert len(first) == 1
    assert len(classify_calls) == 1

    # Mutating returned record must not corrupt cached copy
    first[0].verdict = "mutated"

    clock[0] += 30.0  # < 60.0s TTL
    second = worktree_gc.audit_worktrees(str(repo), with_sizes=False)
    assert len(second) == 1
    assert second[0].verdict == "reap"
    assert len(classify_calls) == 1


def test_directory_entry_change_or_ttl_expiry_invalidates_audit_cache(repo, monkeypatch):
    _add_wt(repo, "wt-one")
    clock = [1000.0]
    monkeypatch.setattr(worktree_gc, "_audit_monotonic", lambda: clock[0])

    classify_calls = []
    real_classify = worktree_gc._classify_tree
    monkeypatch.setattr(
        worktree_gc, "_classify_tree", lambda *a, **k: (classify_calls.append(1), real_classify(*a, **k))[1]
    )

    assert len(worktree_gc.audit_worktrees(str(repo), with_sizes=False)) == 1
    assert len(classify_calls) == 1

    # Adding a worktree under .worktrees/ changes entry count/names -> immediate fresh audit
    _add_wt(repo, "wt-two")
    assert len(worktree_gc.audit_worktrees(str(repo), with_sizes=False)) == 2
    assert len(classify_calls) == 3  # classified both wt-one and wt-two

    # Advancing past 60.0s TTL triggers fresh audit even with unchanged .worktrees/
    clock[0] += 61.0
    assert len(worktree_gc.audit_worktrees(str(repo), with_sizes=False)) == 2
    assert len(classify_calls) == 5


def test_reclaim_worktrees_bypasses_cache_and_invalidates_after_mutation(repo, monkeypatch):
    wt = _add_wt(repo, "wt-reclaim")

    # Populate cache first
    cached = worktree_gc.audit_worktrees(str(repo), with_sizes=False)
    assert len(cached) == 1 and cached[0].verdict == "reap"

    # Make the worktree dirty IN PLACE (without changing .worktrees/ parent dir entry count!)
    # Destructive reclaim(records=None) MUST pass use_cache=False so it sees the uncommitted edit and keeps it!
    (wt / "README.md").write_text("uncommitted important work\n", encoding="utf-8")

    seen_use_cache: list[bool] = []
    real_audit = worktree_gc.audit_worktrees

    def spy_audit(*args, **kwargs):
        seen_use_cache.append(kwargs.get("use_cache", True))
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(worktree_gc, "audit_worktrees", spy_audit)

    actions = worktree_gc.reclaim_worktrees(str(repo))
    assert seen_use_cache == [False]
    assert actions == []
    assert wt.exists(), "dirty worktree must NOT be deleted using stale cached 'reap' verdict"

    # Revert dirt and reclaim -> cache is invalidated so subsequent audit_worktrees returns []
    (wt / "README.md").write_text("init\n", encoding="utf-8", newline="\n")
    actions2 = worktree_gc.reclaim_worktrees(str(repo))
    assert any("removed wt-reclaim" in a for a in actions2)
    assert not wt.exists()
    assert real_audit(str(repo), with_sizes=False) == []
