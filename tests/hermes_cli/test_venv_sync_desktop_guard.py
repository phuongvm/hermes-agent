"""The launch-time source-update tail never stops a running Hermes Desktop.

``prepare_launch`` -> ``_finish_source_update`` runs automatically in front of
whatever command the user typed, after HEAD or ``uv.lock`` moved on disk. With
``--desktop`` the tail repackages ``apps/desktop/release``; on Windows the exe
lock makes ``build_prepared_desktop`` stop every Desktop from that tree that is
not one of our ancestors (``_stop_desktop_processes_locking_build``), i.e. the
user's live session. The tail must therefore drop ``--desktop`` while a Desktop
runs from the release tree, and only then.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from hermes_cli import venv_sync


# --------------------------------------------------------------------------- helpers


class _FakeProc:
    """A psutil.Process stand-in that fails loudly if anything tries to signal it."""

    def __init__(self, pid: int, exe: str | None, *, info_error: bool = False):
        self.pid = pid
        self._exe = exe
        self._info_error = info_error
        self.signalled: list[str] = []

    @property
    def info(self):
        if self._info_error:
            raise RuntimeError("process vanished while being read")
        return {"pid": self.pid, "exe": self._exe}

    def exe(self):
        return self._exe

    def terminate(self):
        self.signalled.append("terminate")

    def kill(self):
        self.signalled.append("kill")


def _desktop_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout with an installed packaged Desktop; returns (root, release exe)."""
    root = tmp_path / "checkout"
    (root / "hermes_cli").mkdir(parents=True)
    exe = root / "apps/desktop/release/win-unpacked/Hermes.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    return root, exe


@pytest.fixture
def tail(tmp_path, monkeypatch):
    """Run ``_finish_source_update`` with the completion child captured, never started."""
    import pm.environments
    from hermes_cli import update_custody

    root, exe = _desktop_checkout(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(venv_sync, "_tree_matches_completed_stamp", lambda root: False)
    monkeypatch.setattr(pm.environments, "activation_environment", lambda root: None)
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(update_custody, "run", run)

    def finish() -> list[str]:
        venv_sync._finish_source_update(root, current=True, pending=root / ".update-pending")
        assert len(calls) == 1, "the tail must start exactly one completion child"
        return calls[-1]

    finish.root = root
    finish.exe = exe
    return finish


# ------------------------------------------------------------- _finish_source_update
#
# The exe-lock guard is Windows behaviour, so its tail arms run on Windows and the
# POSIX arm runs on POSIX (tests/AGENTS.md "Don't fake the host OS"). Only the
# process table is substituted; the host platform is always the real one.


@pytest.mark.platforms("windows")
def test_tail_omits_desktop_while_a_release_desktop_runs_on_windows(tail, monkeypatch, capsys):
    probed: list[Path] = []

    def probe(desktop_app):
        probed.append(desktop_app)
        return [4242]

    monkeypatch.setattr(venv_sync, "_live_desktop_processes_in_release", probe)

    argv = tail()

    assert "--desktop" not in argv, "the tail would repackage (and stop) the running Desktop"
    assert "--finish-update" in argv, "the rest of the tail is still owed and must still run"
    assert probed == [tail.root / "apps/desktop"]
    err = capsys.readouterr().err
    assert "Skipped rebuilding Hermes Desktop" in err
    assert "4242" in err
    assert "hermes desktop" in err, "the notice must tell the user how to rebuild it later"


@pytest.mark.platforms("windows")
def test_tail_passes_desktop_when_no_release_desktop_runs_on_windows(tail, monkeypatch, capsys):
    monkeypatch.setattr(venv_sync, "_live_desktop_processes_in_release", lambda desktop_app: [])

    argv = tail()

    assert "--desktop" in argv
    assert "Skipped rebuilding Hermes Desktop" not in capsys.readouterr().err


@pytest.mark.platforms("linux", "macos")
def test_tail_never_probes_on_posix(tail, monkeypatch):
    """POSIX can rename a running app's files away; the pack never needs the exe lock freed."""

    def probe(desktop_app):
        raise AssertionError("the Windows exe-lock guard must not run on POSIX")

    monkeypatch.setattr(venv_sync, "_live_desktop_processes_in_release", probe)

    assert "--desktop" in tail()


def test_tail_passes_desktop_for_a_source_build_with_no_release_desktop(tail):
    shutil.rmtree(tail.root / "apps/desktop/release")
    dist = tail.root / "apps/desktop/dist/index.html"
    dist.parent.mkdir(parents=True)
    dist.write_text("<html></html>", encoding="utf-8")

    assert "--desktop" in tail()


def test_tail_without_an_installed_desktop_never_probes(tail, monkeypatch):
    shutil.rmtree(tail.root / "apps/desktop")

    def probe(desktop_app):
        raise AssertionError("no Desktop is installed: nothing to probe")

    monkeypatch.setattr(venv_sync, "_live_desktop_processes_in_release", probe)

    assert "--desktop" not in tail()


# ------------------------------------------------- the decision, platform as data


def test_exe_lock_predicate_takes_the_platform_as_data():
    assert venv_sync._desktop_exe_locks_release("win32") is True
    assert venv_sync._desktop_exe_locks_release("linux") is False
    assert venv_sync._desktop_exe_locks_release("darwin") is False


@pytest.mark.platforms("windows")
def test_exe_lock_predicate_defaults_to_this_host_on_windows():
    assert venv_sync._desktop_exe_locks_release() is True


@pytest.mark.platforms("linux", "macos")
def test_exe_lock_predicate_defaults_to_this_host_on_posix():
    assert venv_sync._desktop_exe_locks_release() is False


def test_desktop_tail_requested_end_to_end_with_the_real_probe(tmp_path, monkeypatch, capsys):
    """The decision helper with the real probe over a fake process table."""
    import psutil

    root, exe = _desktop_checkout(tmp_path)
    live = _FakeProc(4_000_001, str(exe))
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([live]))
    assert venv_sync._desktop_tail_requested(root / "apps/desktop", exe_locks_release=True) is False
    assert live.signalled == [], "the guard must never signal the Desktop it protects"
    assert "4000001" in capsys.readouterr().err

    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([]))
    assert venv_sync._desktop_tail_requested(root / "apps/desktop", exe_locks_release=True) is True


def test_desktop_tail_requested_without_the_exe_lock_never_probes(tmp_path, monkeypatch):
    root, _ = _desktop_checkout(tmp_path)

    def probe(desktop_app):
        raise AssertionError("no exe lock: nothing to protect, nothing to probe")

    monkeypatch.setattr(venv_sync, "_live_desktop_processes_in_release", probe)
    assert venv_sync._desktop_tail_requested(root / "apps/desktop", exe_locks_release=False) is True


def test_desktop_tail_requested_without_an_installed_desktop(tmp_path):
    assert venv_sync._desktop_tail_requested(tmp_path / "apps/desktop", exe_locks_release=True) is False


# ---------------------------------------------------------- the read-only probe


def test_probe_finds_only_processes_running_from_the_release_tree(tmp_path, monkeypatch):
    import psutil

    root, exe = _desktop_checkout(tmp_path)
    helper = exe.parent / "resources/helper.exe"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"MZ")
    outside = tmp_path / "elsewhere/Hermes.exe"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"MZ")
    procs = [
        _FakeProc(4_000_003, str(helper)),
        _FakeProc(4_000_001, str(exe)),
        _FakeProc(4_000_002, str(outside)),
        _FakeProc(4_000_004, None),  # AccessDenied: psutil reports no exe
        _FakeProc(4_000_005, str(exe), info_error=True),  # exited while iterating
        _FakeProc(os.getpid(), str(exe)),  # never ourselves
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))

    assert venv_sync._live_desktop_processes_in_release(root / "apps/desktop") == [4_000_001, 4_000_003]
    assert all(p.signalled == [] for p in procs), "the probe must be read-only"


def test_probe_without_a_release_tree_is_empty(tmp_path, monkeypatch):
    import psutil

    def boom(attrs=None):
        raise AssertionError("no release tree: the process table must not be walked")

    monkeypatch.setattr(psutil, "process_iter", boom)
    assert venv_sync._live_desktop_processes_in_release(tmp_path / "apps/desktop") == []


def test_probe_reports_unknown_when_the_process_table_is_unreadable(tmp_path, monkeypatch):
    """An unreadable table is "unknown" (None), never "nothing running" ([])."""
    import psutil

    root, _ = _desktop_checkout(tmp_path)

    def denied(attrs=None):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "process_iter", denied)
    assert venv_sync._live_desktop_processes_in_release(root / "apps/desktop") is None


def test_probe_reports_unknown_when_the_walk_breaks_part_way(tmp_path, monkeypatch):
    """A walk cut short may have missed the Desktop: what it did find is not the answer."""
    import psutil

    root, _ = _desktop_checkout(tmp_path)
    outside = tmp_path / "elsewhere/other.exe"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"MZ")

    def broken(attrs=None):
        yield _FakeProc(4_000_002, str(outside))
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "process_iter", broken)
    assert venv_sync._live_desktop_processes_in_release(root / "apps/desktop") is None


def test_probe_reports_unknown_without_psutil(tmp_path, monkeypatch):
    import sys

    root, _ = _desktop_checkout(tmp_path)
    monkeypatch.setitem(sys.modules, "psutil", None)  # `import psutil` raises ImportError
    assert venv_sync._live_desktop_processes_in_release(root / "apps/desktop") is None


# ------------------------------------- the decision fails closed when the probe cannot see


def test_decision_omits_desktop_when_the_process_table_is_unreadable(tmp_path, monkeypatch, capsys):
    """Reviewer repro: AccessDenied on the walk must not read as "no Desktop running"."""
    import psutil

    root, _ = _desktop_checkout(tmp_path)

    def denied(attrs=None):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "process_iter", denied)
    assert venv_sync._desktop_tail_requested(root / "apps/desktop", exe_locks_release=True) is False
    err = capsys.readouterr().err
    assert "Skipped rebuilding Hermes Desktop" in err
    assert "could not check" in err
    assert "hermes desktop" in err


def test_decision_omits_desktop_without_psutil(tmp_path, monkeypatch):
    import sys

    root, _ = _desktop_checkout(tmp_path)
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert venv_sync._desktop_tail_requested(root / "apps/desktop", exe_locks_release=True) is False


def test_decision_still_passes_desktop_for_a_confirmed_empty_table(tmp_path, monkeypatch, capsys):
    import psutil

    root, _ = _desktop_checkout(tmp_path)
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([]))
    assert venv_sync._desktop_tail_requested(root / "apps/desktop", exe_locks_release=True) is True
    assert "Skipped rebuilding Hermes Desktop" not in capsys.readouterr().err


@pytest.mark.platforms("windows")
def test_tail_omits_desktop_when_the_probe_cannot_see_on_windows(tail, monkeypatch, capsys):
    monkeypatch.setattr(venv_sync, "_live_desktop_processes_in_release", lambda desktop_app: None)

    argv = tail()

    assert "--desktop" not in argv, "inspection uncertainty must not re-enter the killer path"
    assert "--finish-update" in argv
    assert "could not check" in capsys.readouterr().err


def test_probe_sees_every_process_the_build_would_stop(tmp_path, monkeypatch):
    """The guard and the killer share one predicate: anything
    ``_stop_desktop_processes_locking_build`` would terminate, the guard reports first."""
    import psutil

    from hermes_cli import main_desktop

    root, exe = _desktop_checkout(tmp_path)
    outside = tmp_path / "elsewhere/Hermes.exe"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"MZ")

    def table():
        return [_FakeProc(4_000_001, str(exe)), _FakeProc(4_000_002, str(outside)),
                _FakeProc(4_000_003, str(exe.parent / "Hermes.exe"))]

    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(table()))
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: (procs, []))
    seen = venv_sync._live_desktop_processes_in_release(root / "apps/desktop")
    stopped = main_desktop._stop_desktop_processes_locking_build(root / "apps/desktop", also_posix=True)

    assert stopped, "fixture sanity: the killer must target the release Desktop"
    assert set(stopped) <= set(seen)


# --------------------------------------------------- native Windows, real process


@pytest.mark.platforms("windows")
def test_a_real_release_process_is_detected_and_survives_the_tail(tail, monkeypatch, capsys):
    """A real process running from ``apps/desktop/release`` (a copy of ping.exe standing in
    for Hermes.exe): the real probe finds it, the tail drops ``--desktop``, and it lives on."""
    ping = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
    if not ping.is_file():
        pytest.skip("no PING.EXE to stand in for Hermes.exe")
    stand_in = tail.exe.parent / "Hermes-standin.exe"
    shutil.copy2(ping, stand_in)
    proc = subprocess.Popen([str(stand_in), "-n", "60", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        seen: list[int] = []
        while time.monotonic() < deadline and proc.pid not in seen:
            seen = venv_sync._live_desktop_processes_in_release(tail.root / "apps/desktop")
            if proc.pid not in seen:
                time.sleep(0.1)
        assert proc.pid in seen, "the real probe did not see the release process"

        argv = tail()

        assert "--desktop" not in argv
        assert str(proc.pid) in capsys.readouterr().err
        assert proc.poll() is None, "the running release process was stopped"
    finally:
        proc.kill()
        proc.wait(timeout=10)
