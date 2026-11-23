"""Integration tests for Issue #7: Nerve plugin disable / hot-reload unloads sticky hooks & runtime."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gateway.run_plugin_rewire import reload_plugins_verb
from hermes_cli import plugins, plugins_activation, plugins_cmd
from hermes_cli.config import load_config, save_config
from tools.registry import registry
from tui_gateway import server as tui_server


def _resolve_nerve_worktree() -> Path:
    explicit = os.environ.get("NERVE_TASK_INTENT_WORKTREE")
    if explicit:
        candidate = Path(explicit).resolve()
        if (candidate / "__init__.py").is_file():
            return candidate
    sibling = Path(__file__).resolve().parents[3] / "wt-nerve-prerequisite-descriptors"
    if (sibling / "__init__.py").is_file():
        return sibling
    pytest.skip("Set NERVE_TASK_INTENT_WORKTREE or create sibling wt-nerve-prerequisite-descriptors")
    raise AssertionError("unreachable")


def _install_nerve_into_home(hermes_home: Path, nerve_root: Path) -> Path:
    target = hermes_home / "plugins" / "nerve"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    shutil.copy2(nerve_root / "plugin.yaml", target / "plugin.yaml")
    shutil.copy2(nerve_root / "__init__.py", target / "__init__.py")
    shutil.copytree(nerve_root / "hermes_nerve", target / "hermes_nerve")
    return target


@pytest.fixture
def nerve_plugin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    nerve_root = _resolve_nerve_worktree()
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    empty_bundled = tmp_path / "empty-bundled"
    empty_bundled.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_NERVE_NERVOUS_EVENTS", str(tmp_path / "nervous-events.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_OUTCOMES", str(tmp_path / "outcomes.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_POLICY_MEMORY", str(tmp_path / "policy.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_PREFERENCES", str(tmp_path / "preferences.json"))
    monkeypatch.setenv("HERMES_NERVE_GATE_EVENTS", str(tmp_path / "gate-events.jsonl"))
    for key in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_TASK_ID", "HERMES_NERVE_REMOTE_CONTROL_FILE"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda _: None)

    def _commit_plugin_selection(enabled: set[str], disabled: set[str], **_kwargs: Any) -> None:
        cfg = load_config()
        cfg.setdefault("plugins", {})["enabled"] = sorted(enabled)
        cfg["plugins"]["disabled"] = sorted(disabled)
        save_config(cfg)

    monkeypatch.setattr("hermes_cli.plugins_admission.admit_plugin_set_change", _commit_plugin_selection)

    _install_nerve_into_home(hermes_home, nerve_root)
    initial_cfg = {
        "plugins": {
            "enabled": ["nerve"],
            "disabled": [],
            "entries": {
                "nerve": {
                    "settings": {
                        "nerve_profile": "fat_cat",
                        "reflex_backend": "laya",
                        "reflex_laya_base_url": "http://127.0.0.1:9",
                        "reflex_laya_timeout_seconds": 0.05,
                        "gate_mode": "advisory",
                        "nervous_enabled": True,
                        "nervous_turn_admission": True,
                        "nervous_emit_prompt_hint": True,
                        "selective_context_enabled": True,
                        "selective_context_memory_mode": "apply",
                    }
                }
            },
        }
    }
    (hermes_home / "config.yaml").write_text(json.dumps(initial_cfg), encoding="utf-8")

    plugins._reset_plugin_managers_for_tests()
    try:
        plugins.discover_plugins(force=True)
        manager = plugins.get_plugin_manager()
        loaded = manager._plugins.get("nerve")
        assert loaded is not None and loaded.enabled and loaded.module is not None
        store = loaded.module.experience.default_store()
        store.create_claim(
            subject="sticky-nerve-memory",
            statement="Disabling Nerve must detach [Nerve selected memory] and nervous supervision in live sessions.",
            claim_type="verified",
            evidence_ids=["ev-issue7"],
            confidence=0.95,
            admit=True,
        )
        yield SimpleNamespace(home=hermes_home, manager=manager)
    finally:
        plugins._reset_plugin_managers_for_tests()


def _pre_llm_text(session_id: str, turn_id: str) -> str:
    results = plugins.invoke_hook(
        "pre_llm_call",
        session_id=session_id,
        turn_id=turn_id,
        user_message="verify sticky-nerve-memory hook behavior",
    )
    return "\n".join(
        r["context"] if isinstance(r, dict) and "context" in r else str(r)
        for r in results
        if r is not None
    )


def test_cli_disable_notifies_serve_backend_and_unloads_sticky_nerve_hooks(
    nerve_plugin_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces ae56b4 / c28c44: `hermes plugins disable nerve` from CLI while Desktop `serve` runs."""
    manager = nerve_plugin_env.manager
    nerve_mod = manager._plugins["nerve"].module
    assert nerve_mod is not None

    before_text = _pre_llm_text("ae56b4", "turn-1")
    assert "[Nerve selected memory" in before_text
    assert "Nerve nervous supervision is active" in before_text
    assert nerve_mod.nervous.report()["enabled"] is True
    assert nerve_mod.nervous.report()["admission_enabled"] is True
    assert plugins.has_hook("pre_tool_call") is True
    assert plugins.has_middleware("llm_request") is True
    assert registry.get_entry("nerve_stats", scope=manager.scope_key) is not None

    serve_notifications: list[tuple[str, Path]] = []
    gateway_reloads: list[Path] = []

    def _fake_notify_serve_backend(name: str, home: Path):
        serve_notifications.append((name, Path(home)))
        return {"ok": True, "activation": plugins_activation.load_and_go_live(name)}

    def _fake_reload_gateway(target: Path, **_kwargs: Any):
        gateway_reloads.append(Path(target))  # Desktop-only box: no standalone messaging gateway running

    monkeypatch.setattr(plugins_activation, "notify_serve_backend", _fake_notify_serve_backend)
    monkeypatch.setattr("gateway.control_socket.reload_gateway_plugins", _fake_reload_gateway)

    plugins_cmd.cmd_disable("nerve")

    assert serve_notifications and serve_notifications[0][0] == "nerve"
    assert _pre_llm_text("ae56b4", "turn-2") == ""
    assert plugins.has_hook("pre_tool_call") is False
    assert plugins.has_middleware("llm_request") is False
    assert plugins.get_plugin_context_engine() is None
    assert registry.get_entry("nerve_stats", scope=manager.scope_key) is None
    report_after = nerve_mod.nervous.report()
    assert report_after["enabled"] is False
    assert report_after["admission_enabled"] is False


def test_desktop_plugins_manage_toggle_disable_unloads_nerve_in_process_and_reenables(
    nerve_plugin_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toggling Nerve off via Desktop `plugins.manage` unloads hooks & runtime immediately in-process."""
    manager = nerve_plugin_env.manager
    nerve_mod = manager._plugins["nerve"].module
    assert nerve_mod is not None

    assert "[Nerve selected memory" in _pre_llm_text("c28c44", "turn-1")
    assert nerve_mod.nervous.report()["enabled"] is True
    assert nerve_mod.nervous.report()["admission_enabled"] is True

    gateway_reloads: list[Path] = []
    monkeypatch.setattr(
        "gateway.control_socket.reload_gateway_plugins",
        lambda target, **_kw: gateway_reloads.append(Path(target)) or {"reloaded": True, "plugins": []},
    )

    resp_off = tui_server.handle_request(
        {"id": "off-1", "method": "plugins.manage", "params": {"action": "toggle", "key": "nerve", "enable": False}}
    )
    assert resp_off["result"]["ok"] is True
    assert gateway_reloads, "Desktop toggle disable must also nudge any running gateway"

    assert _pre_llm_text("c28c44", "turn-2") == ""
    assert plugins.has_hook("pre_tool_call") is False
    assert plugins.has_middleware("llm_request") is False
    assert registry.get_entry("nerve_stats", scope=manager.scope_key) is None
    assert nerve_mod.nervous.report()["enabled"] is False
    assert nerve_mod.nervous.report()["admission_enabled"] is False
    # Re-enabling via plugins.manage toggle restores Nerve cleanly in the same process.
    resp_on = tui_server.handle_request(
        {"id": "on-1", "method": "plugins.manage", "params": {"action": "toggle", "key": "nerve", "enable": True}}
    )
    assert resp_on["result"]["ok"] is True
    reloaded_mod = manager._plugins["nerve"].module
    assert reloaded_mod is not None
    assert reloaded_mod.nervous.report()["enabled"] is True
    assert reloaded_mod.nervous.report()["admission_enabled"] is True
    assert "[Nerve selected memory" in _pre_llm_text("c28c44", "turn-3")


def test_gateway_reload_plugins_verb_tears_down_disabled_nerve_runtime(
    nerve_plugin_env: SimpleNamespace,
) -> None:
    """Control-socket `reload-plugins` detaches Nerve hooks and flips nervous.enabled=False."""
    manager = nerve_plugin_env.manager
    nerve_mod = manager._plugins["nerve"].module
    assert nerve_mod is not None
    assert nerve_mod.nervous.report()["enabled"] is True

    cfg = load_config()
    cfg["plugins"]["enabled"] = []
    cfg["plugins"]["disabled"] = ["nerve"]
    save_config(cfg)

    loop = asyncio.new_event_loop()
    try:
        runner = SimpleNamespace(adapters={}, _served_profile_homes={}, config=SimpleNamespace(multiplex_profiles=False))
        handler = reload_plugins_verb(runner, loop)
        # Run loop on a background thread so _count_adapters coroutine resolves cleanly.
        import threading

        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            answer = handler({"home": str(nerve_plugin_env.home)})
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2.0)
    finally:
        loop.close()

    assert answer["reloaded"] is True
    assert manager._plugins["nerve"].enabled is False
    assert "nerve" not in [a["key"] for a in answer["activations"]]
    assert _pre_llm_text("gw-1", "turn-after-reload") == ""
    assert nerve_mod.nervous.report()["enabled"] is False
    assert nerve_mod.nervous.report()["admission_enabled"] is False


def test_plugins_manage_remove_unloads_nerve_hooks_and_runtime(
    nerve_plugin_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uninstalling Nerve via `plugins.manage remove` detaches hooks and shuts down live runtime."""
    manager = nerve_plugin_env.manager
    nerve_mod = manager._plugins["nerve"].module
    assert nerve_mod is not None
    assert "[Nerve selected memory" in _pre_llm_text("rm-1", "turn-1")
    assert nerve_mod.nervous.report()["enabled"] is True

    monkeypatch.setattr("gateway.control_socket.reload_gateway_plugins", lambda *_a, **_kw: {"reloaded": True, "plugins": []})

    resp = tui_server.handle_request(
        {"id": "rm-1", "method": "plugins.manage", "params": {"action": "remove", "name": "nerve"}}
    )
    assert resp["result"]["ok"] is True
    assert _pre_llm_text("rm-1", "turn-2") == ""
    assert plugins.has_hook("pre_tool_call") is False
    assert plugins.has_middleware("llm_request") is False
    assert registry.get_entry("nerve_stats", scope=manager.scope_key) is None
    assert nerve_mod.nervous.report()["enabled"] is False
    assert nerve_mod.nervous.report()["admission_enabled"] is False
