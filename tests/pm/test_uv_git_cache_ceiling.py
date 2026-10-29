"""Regression tests for nested git-cache ceiling isolation and plugin-eviction safety."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import hermes_yaml as yaml

import pm.paths
import pm.workspace as ws
from pm.environment import PythonEnvironment
from pm.package import InstallError
from tests.pm import _fixtures


def _uv_and_git_available() -> bool:
    return shutil.which("uv") is not None and shutil.which("git") is not None


def _write_enabled(home: Path, enabled: list[str]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    with (home / "config.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump({"plugins": {"enabled": enabled}}, handle)


@pytest.fixture
def isolated_pm_env(tmp_path, monkeypatch):
    """Temp core + HERMES_HOME wiring identical to test_plugin_survival_contract."""
    core = tmp_path / "core"
    core.mkdir()
    home = tmp_path / "home"
    _write_enabled(home, [])

    ensure = importlib.import_module("pm.install")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
    monkeypatch.setattr(pm.paths, "repo_root", lambda: core)
    monkeypatch.setattr(ws.paths, "repo_root", lambda: core)
    monkeypatch.setattr(ensure, "lazy_installs_allowed", lambda: True)
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "tools"))

    def in_process_sync(*args, project_root=None, **kwargs):
        assert project_root is None or Path(project_root).resolve() == core.resolve()
        return ensure.sync_venv(*args, **kwargs)

    def _toolchain(**kwargs):
        uv_exe = shutil.which("uv")
        assert uv_exe is not None
        return Path(uv_exe), Path(sys.executable)

    monkeypatch.setattr("pm.client.sync_venv", in_process_sync)
    monkeypatch.setattr("pm._uv._toolchain", _toolchain)
    return tmp_path, core, home


@pytest.mark.skipif(not _uv_and_git_available(), reason="uv and git required")
def test_uv_git_cache_nested_under_parent_git_repo_self_heals_empty_db_bucket(tmp_path, monkeypatch):
    """When UV_CACHE_DIR lives inside an enclosing git worktree (e.g. O:/workspaces/_config/...)
    and an aborted `git init` left an empty `git-v1/db/<bucket>` directory, `PythonEnvironment.lock`
    must fence upward `.git` discovery at `self.cache` so git does not walk into the parent repo,
    pollute its object store, and wedge `git clone` with exit code 128."""
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
    git = shutil.which("git")
    uv = shutil.which("uv")
    assert git and uv

    enclosing = tmp_path / "monorepo"
    enclosing.mkdir()
    subprocess.run([git, "init", str(enclosing)], check=True, capture_output=True, timeout=30)

    origin = tmp_path / "psutil_origin"
    origin.mkdir()
    subprocess.run([git, "init", str(origin)], check=True, capture_output=True, timeout=30)
    subprocess.run([git, "-C", str(origin), "config", "user.email", "t@example.com"], check=True)
    subprocess.run([git, "-C", str(origin), "config", "user.name", "test"], check=True)
    (origin / "pyproject.toml").write_text(
        '[project]\nname = "psutil"\nversion = "7.2.2"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    subprocess.run([git, "-C", str(origin), "add", "."], check=True)
    subprocess.run([git, "-C", str(origin), "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        [git, "-C", str(origin), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    url = origin.as_uri()

    seed_cache = tmp_path / "seed_cache"
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        f'[project]\nname = "hermes-agent"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
        f'dependencies = ["psutil @ git+{url}@{sha} ; sys_platform == \'android\'"]\n'
        f'[tool.uv]\npackage = false\nenvironments = ["sys_platform != \'android\'"]\n'
        f'exclude-newer = "3 days"\n',
        encoding="utf-8",
    )
    seed_env = PythonEnvironment(
        uv=Path(uv), python=Path(sys.executable),
        destination=tmp_path / "seed_venv", cache=seed_cache, env=dict(os.environ),
    )
    seed_env.lock(proj)
    bucket_id = next((seed_cache / "git-v1" / "db").iterdir()).name

    member = proj / "plugin-deps" / "langfuse"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        '[project]\nname = "hermes-plugin-langfuse"\nversion = "0.0.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n[tool.uv]\npackage = false\n',
        encoding="utf-8",
    )
    (proj / "pyproject.toml").write_text(
        f'[project]\nname = "hermes-agent"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
        f'dependencies = ["psutil @ git+{url}@{sha} ; sys_platform == \'android\'"]\n'
        f'[tool.uv]\npackage = false\nenvironments = ["sys_platform == {sys.platform!r}"]\n'
        f'[tool.uv.workspace]\nmembers = ["plugin-deps/langfuse"]\n',
        encoding="utf-8",
    )

    nested_cache = enclosing / "_config" / "cache" / "uv"
    (nested_cache / "git-v1" / "db" / bucket_id).mkdir(parents=True)

    target_env = PythonEnvironment(
        uv=Path(uv), python=Path(sys.executable),
        destination=tmp_path / "target_venv", cache=nested_cache, env=dict(os.environ),
    )
    target_env.lock(proj)

    polluted = subprocess.run(
        [git, "-C", str(enclosing), "cat-file", "-e", sha],
        capture_output=True, check=False,
    )
    assert polluted.returncode != 0, "enclosing parent git repo must never receive uv cache git objects"


@pytest.mark.skipif(not _uv_and_git_available(), reason="uv and git required")
def test_core_lock_failure_does_not_falsely_evict_enabled_plugins(isolated_pm_env, monkeypatch):
    """When core's frozen lock installs (`plugin_dirs=[]` uses `frozen=True`), but re-locking
    core's own manifest fails on a core git/network source (`frozen=False`), `sync_evicting`
    must fail the sync rather than blaming and disabling healthy plugins across profiles."""
    from pm.install import sync_venv

    tmp_path, core, home = isolated_pm_env
    monkeypatch.setenv("UV_HTTP_RETRIES", "0")
    git = shutil.which("git")
    uv = shutil.which("uv")
    assert git and uv

    # Build a valid initial lock against a temporary git repo, then delete that origin repo:
    # `frozen=True` (core alone, `sys_platform == 'android'` never installed) still succeeds,
    # while ANY `uv lock` (`frozen=False`) fails trying to fetch core's deleted git repo.
    origin = tmp_path / "core_git_dep"
    origin.mkdir()
    subprocess.run([git, "init", str(origin)], check=True, capture_output=True)
    subprocess.run([git, "-C", str(origin), "config", "user.email", "t@example.com"], check=True)
    subprocess.run([git, "-C", str(origin), "config", "user.name", "test"], check=True)
    (origin / "pyproject.toml").write_text(
        '[project]\nname = "core-android-only"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    subprocess.run([git, "-C", str(origin), "add", "."], check=True)
    subprocess.run([git, "-C", str(origin), "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        [git, "-C", str(origin), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    url = origin.as_uri()

    (core / "pyproject.toml").write_text(
        '[project]\nname = "fake-core"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
        f'dependencies = ["core-android-only @ git+{url}@{sha} ; sys_platform == \'android\'"]\n'
        '[tool.uv]\npackage = false\nenvironments = ["sys_platform != \'android\'"]\nexclude-newer = "3 days"\n',
        encoding="utf-8",
    )
    seed_cache = tmp_path / "seed_only_cache"
    subprocess.run(
        [uv, "lock", "--cache-dir", str(seed_cache)],
        cwd=core, check=True, capture_output=True, text=True, timeout=60,
    )
    import stat

    def _force_remove(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    shutil.rmtree(origin, onexc=_force_remove)

    _fixtures.installing_member(home / "plugins" / "healthy-plug", "healthy-plug", tmp_path / "wheels")
    _write_enabled(home, ["healthy-plug"])
    profile = home / "profiles" / "work"
    _fixtures.installing_member(profile / "plugins" / "healthy-plug", "healthy-plug", tmp_path / "wheels")
    _write_enabled(profile, ["healthy-plug"])

    with pytest.raises(InstallError):
        sync_venv(explicit=True, evict_incompatible_plugins=True)

    for target_home in (home, profile):
        cfg = yaml.safe_load((target_home / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["plugins"]["enabled"] == ["healthy-plug"]
        assert not cfg["plugins"].get("disabled")
        assert "disabled_by_update" not in cfg["plugins"]


@pytest.mark.skipif(not _uv_and_git_available(), reason="uv and git required")
def test_identical_plugin_across_profiles_is_not_retried_per_profile(isolated_pm_env, monkeypatch):
    """When N profiles carry identical copies of an unreachable/broken plugin, `sync_evicting`
    must reuse the failed trial verdict across identical declarations against the same fitting
    set instead of running `2 * N` duplicate `uv lock` builds."""
    from pm.install import sync_venv
    from pm.packages import Venv

    tmp_path, core, home = isolated_pm_env
    uv_exe = shutil.which("uv")
    assert uv_exe is not None
    (core / "pyproject.toml").write_text(
        '[project]\nname = "fake-core"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
        'dependencies = []\n[tool.uv]\npackage = false\n',
        encoding="utf-8",
    )
    subprocess.run(
        [uv_exe, "lock"],
        cwd=core, check=True, capture_output=True, text=True, timeout=60,
    )

    missing_repo = (tmp_path / "missing_git_dep").as_uri()
    homes = [home, *(home / "profiles" / f"p{i}" for i in range(4))]
    for target_home in homes:
        plugin = target_home / "plugins" / "offline-dep"
        plugin.mkdir(parents=True)
        (plugin / "pyproject.toml").write_text(
            '[project]\nname="offline-dep"\nversion="1"\nrequires-python=">=3.11"\n'
            'dependencies=["gone"]\n[tool.uv]\npackage=false\n'
            f'[tool.uv.sources]\ngone = {{ git = "{missing_repo}" }}\n',
            encoding="utf-8",
        )
        _write_enabled(target_home, ["offline-dep"])

    trials: list[list[str]] = []
    real_apply = Venv.apply

    def counting_apply(self, extras, *, plugin_dirs=None, **kwargs):
        if plugin_dirs:
            trials.append([Path(p).name for p in plugin_dirs])
        return real_apply(self, extras, plugin_dirs=plugin_dirs, **kwargs)

    monkeypatch.setattr(Venv, "apply", counting_apply)

    sync_venv(explicit=True, evict_incompatible_plugins=True)

    for target_home in homes:
        cfg = yaml.safe_load((target_home / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["plugins"]["disabled"] == ["offline-dep"]
        assert "offline-dep" in cfg["plugins"]["disabled_by_update"]
    # 1 initial union attempt + 2 attempts for the unique declaration (NOT 1 + 5*2 = 11).
    assert len(trials) == 3, f"expected 1 union + 2 trial builds, got {len(trials)}: {trials}"
