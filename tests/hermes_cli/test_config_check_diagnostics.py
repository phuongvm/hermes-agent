"""Config check reports stale saved capabilities without changing profile state."""

from pathlib import Path

import pytest

import hermes_cli.config as config_mod
import hermes_cli.plugins as plugins_mod
from hermes_cli.config import DEFAULT_CONFIG, _cmd_config_check, _warn_invalid_platform_toolsets


def _write_home(home: Path, body: str, env: str = "") -> Path:
    home.mkdir()
    (home / "config.yaml").write_text(f"_config_version: {DEFAULT_CONFIG['_config_version']}\n{body}", encoding="utf-8")
    if env:
        (home / ".env").write_text(env, encoding="utf-8")
    return home


def _check(home: Path, monkeypatch, capsys) -> str:
    monkeypatch.setenv("HERMES_HOME", str(home))
    _cmd_config_check(None)
    return capsys.readouterr().out


@pytest.mark.parametrize("layered", [False, True], ids=["managed-only", "layered"])
def test_config_check_diagnoses_effective_prerequisite_catalog(tmp_path, monkeypatch, capsys, layered):
    from json import dumps

    from tests.tools.proposal_catalog_fixture import CHANGE, profile_config, proposal_entry

    home = tmp_path / "user"
    managed = tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    config = profile_config(proposal_entry(tmp_path / "planning")) if layered else {}
    overlay = {"openspec-propose": {CHANGE: None}} if layered else {"must-not-echo": {}}
    (home / "config.yaml").write_text(dumps(config), encoding="utf-8")
    (managed / "config.yaml").write_text(dumps({"prerequisite_catalog": overlay}), encoding="utf-8")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    output = _check(home, monkeypatch, capsys)
    expected = "invalid-entry" if layered else "invalid-workflow-map"
    assert f"prerequisite_catalog: {expected}" in output
    assert "must-not-echo" not in output


@pytest.mark.parametrize("broken", [False, True], ids=["absent", "unreadable-present"])
def test_managed_catalog_requires_readable_user_layer(tmp_path, monkeypatch, capsys, caplog, broken):
    from json import dumps

    from agent.prerequisite.workflow import _selection
    from hermes_cli.config_effective import load_user_config_effective
    from tests.tools.proposal_catalog_fixture import CHANGE, profile_config, proposal_entry

    home, managed = tmp_path / "user", tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    user_path = home / "config.yaml"
    config = profile_config(proposal_entry(tmp_path / "planning"))
    (managed / "config.yaml").write_text(dumps(config), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    if broken:
        try:
            user_path.symlink_to(tmp_path / "missing-user-config")
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            # Exercise a present entry with a missing target even without symlink privileges.
            user_path.touch()
            original = type(user_path).stat

            def stat(path, *args, **kwargs):
                if path == user_path and kwargs.get("follow_symlinks", True):
                    raise FileNotFoundError("fixture target missing")
                return original(path, *args, **kwargs)

            from hermes_cli.config_backend import FileBackend

            read_path = FileBackend.read_path
            exists = FileBackend.exists

            def exists_following_link(backend, home):
                return backend.config_path(home) != user_path and exists(backend, home)

            def read(path):
                if path == user_path:
                    raise FileNotFoundError("fixture target missing")
                return read_path(path)

            monkeypatch.setattr(type(user_path), "stat", stat)
            monkeypatch.setattr(FileBackend, "read_path", staticmethod(read))
            monkeypatch.setattr(FileBackend, "exists", exists_following_link)
    assert load_user_config_effective()["prerequisite_catalog"] == config["prerequisite_catalog"]
    caplog.set_level("INFO", logger=_selection.__module__)
    assert (_selection(CHANGE) is None) is broken
    assert ("PREREQUISITE_SELECTION_UNAVAILABLE: profile-config-unavailable" in caplog.messages) is broken
    output = _check(home, monkeypatch, capsys)
    assert ("prerequisite_catalog: profile-config-unavailable" in output) is broken
    assert "missing-user-config" not in output


def test_non_directory_home_rejects_managed_catalog(tmp_path, monkeypatch, caplog):
    from json import dumps

    from agent.prerequisite.workflow import _selection
    from hermes_cli.config_check_diagnostics import _effective_catalog_diagnostics
    from tests.tools.proposal_catalog_fixture import CHANGE, profile_config, proposal_entry

    home, managed = tmp_path / "not-a-directory", tmp_path / "managed"
    home.touch()
    managed.mkdir()
    (managed / "config.yaml").write_text(dumps(profile_config(proposal_entry(tmp_path / "planning"))), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    caplog.set_level("INFO", logger=_selection.__module__)
    assert _selection(CHANGE) is None
    assert "PREREQUISITE_SELECTION_UNAVAILABLE: profile-config-unavailable" in caplog.messages
    # The full CLI already refuses this home at initialization, before catalog diagnostics.
    assert _effective_catalog_diagnostics() == ["prerequisite_catalog: profile-config-unavailable"]


def test_catalog_diagnostics_report_backend_read_conflict(monkeypatch):
    from types import SimpleNamespace

    from hermes_cli import config_backend
    from hermes_cli.config_check_diagnostics import _effective_catalog_diagnostics

    def read_conflict(home):
        raise config_backend.ConfigWriteError("must-not-echo", code="config_version_conflict")

    backend = SimpleNamespace(read_user_doc=read_conflict)
    monkeypatch.setattr(config_backend, "get_config_backend", lambda: backend)
    assert _effective_catalog_diagnostics() == ["prerequisite_catalog: profile-config-unavailable"]


def test_config_check_and_migrate_agree_on_stale_platform_toolsets(tmp_path, monkeypatch, capsys):
    # Only a plugin MCP server named 'portable' exists; keep host-installed plugins out of the verdict.
    monkeypatch.setattr(plugins_mod, "get_plugin_toolset_keys_nowait", set)
    monkeypatch.setattr(plugins_mod, "get_portable_mcp_server_names_nowait", lambda: {"portable"})
    stale = _write_home(
        tmp_path / "stale",
        "platform_toolsets:\n"
        "  cli: [hermes-cli, messaging, linear, mcp-linear, mcp-portable, mcp-ghost, ghost]\n"
        "  teams: [hermes-teams]\n"
        "mcp_servers:\n"
        "  linear:\n"
        "    command: linear-mcp\n"
        "    enabled: false\n"
        # Bookkeeping for a plugin that is no longer installed is not proof its toolset exists.
        "known_plugin_toolsets:\n"
        "  cli: [ghost]\n",
    )
    # A malformed mcp_servers section must not crash the check.
    clean = _write_home(tmp_path / "clean", "platform_toolsets:\n  cli: [hermes-cli]\nmcp_servers: [a]\n")

    for home, expected in ((stale, {"messaging", "ghost", "mcp-ghost"}), (clean, set()), (stale, {"messaging", "ghost", "mcp-ghost"})):
        output = _check(home, monkeypatch, capsys)
        for name in ("messaging", "ghost", "mcp-ghost", "linear", "mcp-linear", "mcp-portable", "hermes-teams"):
            assert (f"unknown toolset '{name}'" in output) is (name in expected), (home.name, name)

        results = {"warnings": []}
        _warn_invalid_platform_toolsets(results, quiet=True)
        assert {w for w in results["warnings"] if "unknown toolset" in w} == {
            line.strip().removeprefix("⚠ ") for line in output.splitlines() if "unknown toolset" in line
        }


def test_config_check_reports_disabled_platform_only_when_runtime_disables_it(tmp_path, monkeypatch, capsys):
    manifest = {
        "name": "fakechat-platform",
        "requires_env": [{"name": "FAKECHAT_TOKEN"}],
        "optional_env": [{"name": "FAKECHAT_HOME_CHANNEL"}],
    }
    monkeypatch.setattr(config_mod, "_platform_plugin_manifests", lambda *a, **k: iter([("fakechat", manifest)]))
    monkeypatch.delenv("FAKECHAT_TOKEN", raising=False)
    token = "FAKECHAT_TOKEN=synthetic-test-token\n"
    cases = (
        (_write_home(tmp_path / "key", "plugins:\n  disabled: [platforms/fakechat]\n", token), True),
        (_write_home(tmp_path / "name", "plugins:\n  disabled: [fakechat-platform]\n", token), True),
        (_write_home(tmp_path / "no_token", "plugins:\n  disabled: [platforms/fakechat]\n"), False),
        # The runtime ignores a non-list plugins.disabled, so the platform is not disabled.
        (_write_home(tmp_path / "string", "plugins:\n  disabled: platforms/fakechat\n", token), False),
    )

    for home, reported in (*cases, cases[0]):
        output = _check(home, monkeypatch, capsys)
        assert ("platform plugin 'platforms/fakechat' is disabled" in output) is reported, home.name
        if reported:
            assert "hermes plugins enable platforms/fakechat" in output
        assert "synthetic-test-token" not in output
