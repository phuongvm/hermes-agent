"""Dashboard chat ``profile`` scope: naming the dashboard's OWN profile is not a cross-profile chat.

The Desktop names its active profile explicitly on every ``/api/pty?profile=<name>`` request.
When that name resolves back to this process's own ``HERMES_HOME`` the chat must stay attached to
the in-memory gateway (``HERMES_TUI_GATEWAY_URL``) with no ``HERMES_HOME`` override — treating it as
a foreign profile detached the chat and bootstrapped a second gateway for the same home.
"""

from pathlib import Path

import pytest

from hermes_cli import web_server_chat, web_server_profiles

GATEWAY_URL = "ws://127.0.0.1:9/api/gateway/ws?token=t"


@pytest.fixture
def chat_env(monkeypatch, tmp_path):
    import hermes_cli.main_tui_launch as tui_launch

    own_home = tmp_path / "home"
    other_home = tmp_path / "profiles" / "ops"
    own_home.mkdir(parents=True)
    other_home.mkdir(parents=True)
    dirs = {"self": own_home, "ops": other_home}

    monkeypatch.setattr(
        tui_launch, "_make_tui_argv", lambda *_a, **_k: (["node", "fake-tui.js"], tmp_path))
    monkeypatch.setattr(tui_launch, "_apply_tui_python_env", lambda _env: None)
    monkeypatch.setattr(web_server_chat, "_build_gateway_ws_url", lambda: GATEWAY_URL)
    monkeypatch.setattr(web_server_profiles, "get_process_hermes_home", lambda: own_home)
    monkeypatch.setattr(web_server_profiles, "_resolve_profile_dir", lambda name: dirs[name])
    return dirs


def test_own_profile_name_stays_on_the_in_memory_gateway(chat_env):
    _argv, _cwd, env = web_server_chat._resolve_chat_argv(profile="self")

    assert env["HERMES_TUI_GATEWAY_URL"] == GATEWAY_URL
    assert Path(env.get("HERMES_HOME", "")) != chat_env["ops"]


def test_other_profile_name_scopes_hermes_home_and_skips_the_attach(chat_env):
    _argv, _cwd, env = web_server_chat._resolve_chat_argv(profile="ops")

    assert env["HERMES_HOME"] == str(chat_env["ops"])
    assert "HERMES_TUI_GATEWAY_URL" not in env


@pytest.mark.parametrize("profile", [None, "", "current", "CURRENT"])
def test_unnamed_profile_is_the_current_one(chat_env, profile):
    _argv, _cwd, env = web_server_chat._resolve_chat_argv(profile=profile)

    assert env["HERMES_TUI_GATEWAY_URL"] == GATEWAY_URL
