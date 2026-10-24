"""A shared ``<repo>/.worktrees`` may hold trees of OTHER repositories: worktrees of a submodule
(``.git`` -> ``<super>/.git/modules/<sub>/worktrees/<name>``), of a sibling repository, or a
standalone clone. The startup sweep of ``<repo>`` must leave those alone and stay silent about
them (no ``archive failed`` / ``Preserving N stale worktree(s)`` warnings), while still sweeping
the repo's own trees; and an archive ref is always written into the object store that holds the
snapshot commit (the worktree's own repository).

Real git repositories, no mocks of git.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import pytest

from hermes_cli import worktree_ops, worktree_sweep

_GIT_C = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "protocol.file.allow=always",
          "-c", "core.autocrlf=false"]


def _git(args, cwd):
    result = subprocess.run(["git", *_GIT_C, *args], cwd=str(cwd), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, check=False)
    assert result.returncode == 0, f"git {args}: {result.stderr}"
    return result.stdout.strip()


def _init(path: Path, origin: Path | None = None) -> Path:
    _git(["init", "-q", "-b", "main", str(path)], path.parent)
    (path / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-qm", "init"], path)
    if origin is not None:
        _git(["init", "-q", "--bare", "-b", "main", str(origin)], path.parent)
        _git(["remote", "add", "origin", str(origin)], path)
        _git(["push", "-q", "origin", "main"], path)
        _git(["fetch", "-q", "origin"], path)
    return path


@pytest.fixture
def layout(tmp_path):
    """``super`` (with submodule ``sub``) + sibling repo ``other``, sharing ``super/.worktrees``."""
    sub_src = _init(tmp_path / "sub-src", tmp_path / "sub-origin.git")
    sup = _init(tmp_path / "super", tmp_path / "super-origin.git")
    _git(["submodule", "add", "-q", str(sub_src), "sub"], sup)
    _git(["commit", "-qm", "add sub"], sup)
    _git(["push", "-q", "origin", "main"], sup)
    _git(["fetch", "-q", "origin"], sup)
    other = _init(tmp_path / "other", tmp_path / "other-origin.git")
    (sup / ".worktrees").mkdir()
    return {"super": sup, "sub": sup / "sub", "other": other, "wts": sup / ".worktrees"}


def _add_tree(owner: Path, dest: Path, *, dirty=True) -> Path:
    _git(["worktree", "add", "-q", "-b", f"wt/{dest.name}", str(dest), "HEAD"], owner)
    if dirty:
        (dest / "notes.md").write_text("uncommitted\n", encoding="utf-8")
    return dest


def _idle(path: Path, hours: float = 24 * 10) -> None:
    stamp = time.time() - hours * 3600
    git_dir = Path(_git(["rev-parse", "--absolute-git-dir"], path))
    for item in [*path.rglob("*"), git_dir / "HEAD", git_dir / "logs" / "HEAD", path]:
        if item.exists():
            os.utime(item, (stamp, stamp))


def _archives(repo: Path) -> list[str]:
    out = _git(["for-each-ref", "--format=%(refname)", "refs/hermes/archive/"], repo)
    return [line for line in out.splitlines() if line]


def _set_config(text: str) -> None:
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(text, encoding="utf-8")


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= logging.WARNING]


@pytest.fixture
def cli_log():
    handler = _Capture()
    loggers = [logging.getLogger("cli"), logging.getLogger("hermes_cli.worktree_archive")]
    for lg in loggers:
        lg.addHandler(handler)
    try:
        yield handler
    finally:
        for lg in loggers:
            lg.removeHandler(handler)


def _foreign_trees(layout):
    wts = layout["wts"]
    sub_tree = _add_tree(layout["sub"], wts / "wt-sub-lane")
    sibling_tree = _add_tree(layout["other"], wts / "wt-other-lane")
    clone = wts / "wt-standalone-clone"
    _git(["clone", "-q", str(layout["other"]), str(clone)], wts)
    (clone / "notes.md").write_text("uncommitted\n", encoding="utf-8")
    return sub_tree, sibling_tree, clone


# ------------------------------------------------------------------ ownership classification


def test_submodule_worktree_git_pointer_targets_the_submodule_repo(layout):
    """Fixture sanity: the scenario from the field (``.git`` -> ``.git/modules/<sub>/worktrees``)."""
    tree = _add_tree(layout["sub"], layout["wts"] / "wt-sub-lane")
    link = (tree / ".git").read_text(encoding="utf-8").strip()
    assert link.startswith("gitdir:")
    assert Path(link[len("gitdir:"):].strip()).resolve().parent == (
        layout["super"] / ".git" / "modules" / "sub" / "worktrees").resolve()


def test_owned_worktree_entries_skip_foreign_repositories(layout):
    own = _add_tree(layout["super"], layout["wts"] / "wt-own-lane")
    sub_tree, sibling_tree, clone = _foreign_trees(layout)
    plain = layout["wts"] / "plain-dir"
    plain.mkdir()

    owned = worktree_ops.owned_worktree_entries(str(layout["super"]))
    names = {entry.name for entry in owned}
    assert "wt-own-lane" in names
    assert "plain-dir" in names, "an unattributable dir keeps the pre-existing handling"
    assert not names & {sub_tree.name, sibling_tree.name, clone.name}
    assert worktree_ops.is_foreign_worktree(str(layout["super"]), own) is False
    for foreign in (sub_tree, sibling_tree, clone):
        assert worktree_ops.is_foreign_worktree(str(layout["super"]), foreign) is True, foreign.name


def test_ownership_holds_from_the_submodule_side_too(layout):
    """Seen from the submodule repo, its own tree is owned and the superproject's is foreign."""
    sub_tree = _add_tree(layout["sub"], layout["wts"] / "wt-sub-lane")
    own = _add_tree(layout["super"], layout["wts"] / "wt-own-lane")
    assert worktree_ops.is_foreign_worktree(str(layout["sub"]), sub_tree) is False
    assert worktree_ops.is_foreign_worktree(str(layout["sub"]), own) is True


def test_relative_gitdir_pointer_is_resolved_against_the_tree(layout):
    own = _add_tree(layout["super"], layout["wts"] / "wt-own-rel")
    admin = Path((own / ".git").read_text(encoding="utf-8")[len("gitdir:"):].strip()).resolve()
    (own / ".git").unlink()  # Git for Windows marks it hidden: rewrite, not overwrite
    (own / ".git").write_text(f"gitdir: {os.path.relpath(admin, own)}\n", encoding="utf-8")
    assert worktree_ops.is_foreign_worktree(str(layout["super"]), own) is False


def test_unknown_owner_never_hides_a_tree(tmp_path):
    """Outside a git repository the owner cannot be resolved: nothing is filtered (fail-open to
    the existing, preserve-biased classifier rather than silently hiding trees)."""
    wts = tmp_path / "norepo" / ".worktrees"
    (wts / "lane").mkdir(parents=True)
    (wts / "lane" / ".git").write_text("gitdir: /nowhere/worktrees/lane\n", encoding="utf-8")
    assert [e.name for e in worktree_ops.owned_worktree_entries(str(tmp_path / "norepo"))] == ["lane"]


def test_prune_candidates_exclude_foreign_trees(layout):
    own = _add_tree(layout["super"], layout["wts"] / "wt-own-lane")
    foreign = _foreign_trees(layout)
    for tree in (own, *foreign):
        _idle(tree)
    names = {e.name for e, _m, _f in worktree_ops._prune_candidates(layout["wts"], 24, time.time())}
    assert names == {"wt-own-lane"}


def test_plan_sweep_does_not_list_foreign_trees(layout):
    own = _add_tree(layout["super"], layout["wts"] / "wt-own-lane")
    young_foreign = _add_tree(layout["sub"], layout["wts"] / "wt-sub-young")
    foreign = _foreign_trees(layout)
    for tree in (own, *foreign):
        _idle(tree)
    plan = worktree_sweep.plan_sweep(str(layout["super"]), settings=worktree_sweep.sweep_settings())
    names = {d.name for d in plan}
    assert "wt-own-lane" in names
    assert not names & {young_foreign.name, *(t.name for t in foreign)}


def test_manual_audit_does_not_report_foreign_trees(layout):
    """``hermes worktree list/prune`` (``worktree_gc.audit_worktrees``) uses the same ownership rule."""
    from hermes_cli.worktree_gc import audit_worktrees

    own = _add_tree(layout["super"], layout["wts"] / "wt-own-lane")
    foreign = _foreign_trees(layout)
    names = {r.name for r in audit_worktrees(str(layout["super"]), with_sizes=False)}
    assert own.name in names
    assert not names & {t.name for t in foreign}


# ------------------------------------------------------------------ the sweep, end to end


@pytest.mark.parametrize("mode", ["archive", "classic"])
def test_sweep_leaves_foreign_trees_alone_and_silent(layout, cli_log, mode):
    """The field bug: a sweep of the superproject tried to archive submodule worktrees
    (``Keeping worktree <name>: archive failed (could not create an archive ref ...)``) and
    reported them as ``Preserving N stale worktree(s)``. Foreign trees are not the repo's."""
    if mode == "classic":
        _set_config("worktrees:\n  archive_dirty: false\n")
    foreign = _foreign_trees(layout)
    for tree in foreign:
        _idle(tree)

    worktree_ops._prune_stale_worktrees(str(layout["super"]))

    for tree in foreign:
        assert tree.exists() and (tree / "notes.md").read_text(encoding="utf-8") == "uncommitted\n", tree.name
    warnings = "\n".join(cli_log.warnings())
    assert "archive failed" not in warnings
    assert "could not create an archive ref" not in warnings
    assert "Preserving" not in warnings
    for name in (t.name for t in foreign):
        assert name not in warnings
    assert _archives(layout["super"]) == []
    assert _archives(layout["sub"]) == []
    assert _archives(layout["other"]) == []


def test_sweep_still_archives_the_repos_own_idle_trees(layout, cli_log):
    """Foreign trees (a submodule's, a superproject's) sit next to the repo's own idle tree: only
    the own tree is archived and removed. ``other`` has no submodule, so its trees are archivable."""
    other = layout["other"]
    wts = other / ".worktrees"
    wts.mkdir()
    own = _add_tree(other, wts / "wt-own-lane")
    sub_tree = _add_tree(layout["sub"], wts / "wt-sub-lane")
    super_tree = _add_tree(layout["super"], wts / "wt-super-lane")
    for tree in (own, sub_tree, super_tree):
        _idle(tree)

    worktree_ops._prune_stale_worktrees(str(other))

    assert not own.exists(), "the repo's own idle dirty tree is archived then removed"
    assert [r.rsplit("/", 1)[-1] for r in _archives(other)] == ["wt-own-lane"]
    assert _git(["show", "refs/hermes/archive/wt-own-lane:notes.md"], other) == "uncommitted"
    assert sub_tree.exists() and super_tree.exists()
    assert _archives(layout["sub"]) == [] and _archives(layout["super"]) == []
    warnings = "\n".join(cli_log.warnings())
    assert "archive failed" not in warnings and "Preserving" not in warnings


def test_sweep_from_the_submodule_side_archives_its_own_tree(layout, cli_log):
    """The submodule repo's own sweep is the one that owns (and may archive) its trees, and it
    leaves the superproject's trees alone."""
    (layout["sub"] / ".worktrees").mkdir()
    sub_tree = _add_tree(layout["sub"], layout["sub"] / ".worktrees" / "wt-sub-lane")
    _idle(sub_tree)

    worktree_ops._prune_stale_worktrees(str(layout["sub"]))

    assert not sub_tree.exists()
    assert [r.rsplit("/", 1)[-1] for r in _archives(layout["sub"])] == ["wt-sub-lane"]
    assert "archive failed" not in "\n".join(cli_log.warnings())


# ------------------------------------------------------------------ snapshot_worktree ref placement


def test_snapshot_ref_lands_in_the_object_store_that_holds_the_commit(layout):
    """``commit-tree`` runs in the worktree, so its sha lives in the worktree's repository;
    ``update-ref`` must run there too. Pointed at a foreign repo_root it used to fail with
    ``could not create an archive ref``."""
    from hermes_cli.worktree_archive import snapshot_worktree

    sub_tree = _add_tree(layout["sub"], layout["wts"] / "wt-sub-lane")
    arc = snapshot_worktree(str(layout["super"]), sub_tree, reason="test")

    assert arc.ref == "refs/hermes/archive/wt-sub-lane"
    assert _git(["rev-parse", arc.ref], layout["sub"]) == arc.sha
    assert _git(["cat-file", "-t", arc.sha], layout["sub"]) == "commit"
    assert _archives(layout["super"]) == []
    assert _git(["show", f"{arc.ref}:notes.md"], layout["sub"]) == "uncommitted"


def test_snapshot_of_an_owned_tree_is_visible_from_the_repo_root(layout):
    """Owned trees share refs with the main checkout: behaviour there is unchanged."""
    from hermes_cli.worktree_archive import find_archive, snapshot_worktree

    other = layout["other"]
    own = _add_tree(other, other / ".worktrees" / "wt-own-lane")
    arc = snapshot_worktree(str(other), own, reason="test")
    assert _archives(other) == [arc.ref]
    found = find_archive(str(other), "wt-own-lane")
    assert found is not None and found.sha == arc.sha
    assert found.meta["repo_root"] == str(other)


def test_create_ref_is_called_with_the_worktree_path(layout, monkeypatch):
    from hermes_cli import worktree_archive

    seen = []
    real = worktree_archive._create_ref

    def spy(where, name, sha, env):
        seen.append(where)
        return real(where, name, sha, env)

    monkeypatch.setattr(worktree_archive, "_create_ref", spy)
    other = layout["other"]
    own = _add_tree(other, other / ".worktrees" / "wt-own-lane")
    worktree_archive.snapshot_worktree(str(other), own, reason="test")
    assert [Path(p) for p in seen] == [own]
