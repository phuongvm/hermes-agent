"""Tests for acp_adapter.entry startup wiring."""

import os
import subprocess
import sys
from pathlib import Path

import acp
import pytest

from acp_adapter import entry


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_main_enables_unstable_protocol(monkeypatch):
    calls = {}

    async def fake_run_agent(agent, **kwargs):
        calls["kwargs"] = kwargs

    monkeypatch.setattr(entry, "_setup_logging", lambda: None)
    monkeypatch.setattr(entry, "_load_env", lambda: None)
    monkeypatch.setattr(entry, "_prewarm_agent_runtime", lambda: None)
    monkeypatch.setattr(acp, "run_agent", fake_run_agent)

    entry.main([])

    assert calls["kwargs"]["use_unstable_protocol"] is True


def test_main_skips_configured_mcp_discovery_when_requested(monkeypatch):
    discovery_calls = []

    async def fake_run_agent(agent, **kwargs):
        pass

    monkeypatch.setattr(entry, "_setup_logging", lambda: None)
    monkeypatch.setattr(entry, "_load_env", lambda: None)
    monkeypatch.setattr(entry, "_prewarm_agent_runtime", lambda: None)
    monkeypatch.setenv("HERMES_ACP_SKIP_CONFIGURED_MCP", "1")
    monkeypatch.setattr(
        "tools.mcp_tool.discover_mcp_tools",
        lambda: discovery_calls.append(True),
    )
    monkeypatch.setattr(acp, "run_agent", fake_run_agent)

    entry.main([])

    assert discovery_calls == []


def test_prewarm_imports_run_agent(monkeypatch):
    imported_modules = []
    monkeypatch.setattr(entry.importlib, "import_module", imported_modules.append)

    entry._prewarm_agent_runtime()

    assert imported_modules == ["run_agent"]


@pytest.mark.windows_only
def test_prewarm_loads_enabled_plugin_native_dependencies(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - observability/langfuse\n",
        encoding="utf-8",
    )

    fake_sdk_root = tmp_path / "fake-sdk"
    fake_langfuse = fake_sdk_root / "langfuse"
    fake_langfuse.mkdir(parents=True)
    (fake_langfuse / "__init__.py").write_text(
        "import numpy\n"
        "class Langfuse:\n"
        "    pass\n"
        "def propagate_attributes(**_kwargs):\n"
        "    return None\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["HERMES_ACP_SKIP_CONFIGURED_MCP"] = "1"
    env.pop("OPENBLAS_NUM_THREADS", None)
    pythonpath = [str(fake_sdk_root), str(REPO_ROOT)]
    if existing_pythonpath := env.get("PYTHONPATH"):
        pythonpath.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    script = (
        "import sys\n"
        "from acp_adapter.entry import _load_env, _prewarm_agent_runtime\n"
        "_load_env()\n"
        "_prewarm_agent_runtime()\n"
        "assert 'langfuse' in sys.modules\n"
        "assert 'numpy' in sys.modules\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.windows_only
def test_main_prewarms_before_background_threads(monkeypatch):
    events = []

    async def fake_run_agent(agent, **kwargs):
        events.append("acp")

    monkeypatch.setattr(entry, "_setup_logging", lambda: events.append("logging"))
    monkeypatch.setattr(entry, "_load_env", lambda: events.append("env"))
    monkeypatch.setattr(
        entry,
        "_prewarm_agent_runtime",
        lambda: events.append("prewarm"),
    )
    monkeypatch.delenv("HERMES_ACP_SKIP_CONFIGURED_MCP", raising=False)
    monkeypatch.setattr(
        "hermes_cli.mcp_startup.start_background_mcp_discovery",
        lambda **_kwargs: events.append("mcp"),
    )
    monkeypatch.setattr("acp_adapter.server.HermesACPAgent", lambda: object())
    monkeypatch.setattr(acp, "run_agent", fake_run_agent)

    entry.main([])

    assert events == ["logging", "env", "prewarm", "mcp", "acp"]










def test_main_setup_offers_browser_install_when_tty(monkeypatch):
    """When stdin is a TTY and the user answers yes, model setup is followed
    by a browser-tools bootstrap call."""
    monkeypatch.setattr("hermes_cli.main.main", lambda: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "y")

    bootstrap_calls = []
    monkeypatch.setattr(
        entry,
        "_run_setup_browser",
        lambda assume_yes=False: bootstrap_calls.append(assume_yes) or 0,
    )

    entry.main(["--setup"])

    assert bootstrap_calls == [False]










def test_main_setup_browser_propagates_browser_failure(monkeypatch):
    """If browser install fails, exit code is 1."""
    def fake_ensure(dep, interactive=True):
        return dep != "browser"  # browser fails

    monkeypatch.setattr("hermes_cli.dep_ensure.ensure_dependency", fake_ensure)

    with pytest.raises(SystemExit) as excinfo:
        entry.main(["--setup-browser"])
    assert excinfo.value.code == 1
