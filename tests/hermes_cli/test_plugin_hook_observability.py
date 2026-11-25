"""Tests for plugin hook callback ownership attribution and elapsed-time observability (A1)."""

from __future__ import annotations

import asyncio
import functools
import logging
import threading

import pytest

from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    PluginManifest,
    _PRE_TOOL_CALL_TIMEOUT_BLOCK_MESSAGE,
)
import hermes_cli.plugins_dispatch as dispatch


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes_home"))
    return PluginManager()


def _make_ctx(manager: PluginManager, name: str = "nerve") -> PluginContext:
    manifest = PluginManifest(name=name, version="0.1.0", description=f"{name} plugin")
    return PluginContext(manifest, manager)


class _BoundReceiver:
    def on_post_tool(self, **_kwargs):
        return None


def _partial_target(tag: str, **_kwargs):
    return tag


def test_ownership_recorded_for_bound_partial_and_closure_without_mutating_callable(manager):
    ctx = _make_ctx(manager, "nerve")
    receiver = _BoundReceiver()
    bound_cb = receiver.on_post_tool
    partial_cb = functools.partial(_partial_target, "p1")

    def closure_cb(**_kwargs):
        return None

    h_bound = ctx.register_hook("post_tool_call", bound_cb)
    h_partial = ctx.register_hook("post_tool_call", partial_cb)
    h_closure = ctx.register_hook("post_tool_call", closure_cb)

    assert not hasattr(bound_cb, "_hermes_plugin_name")
    assert "_hermes_plugin_name" not in closure_cb.__dict__

    assert manager._hook_callback_meta(bound_cb)[1] == "nerve"
    assert manager._hook_callback_meta(partial_cb)[1] == "nerve"
    assert manager._hook_callback_meta(closure_cb)[1] == "nerve"
    assert h_bound.active and h_partial.active and h_closure.active


def test_ownership_cleaned_up_on_dispose_and_unload_preserving_shared_callback(manager):
    ctx = _make_ctx(manager, "nerve")

    def shared_cb(**_kwargs):
        return None

    def other_cb(**_kwargs):
        return None

    h1 = ctx.register_hook("pre_tool_call", shared_cb)
    h2 = ctx.register_hook("post_tool_call", shared_cb)
    ctx.register_hook("post_tool_call", other_cb)

    # Disposing one hook keeps ownership while still registered on the second hook.
    h1.dispose()
    assert manager._hook_callback_meta(shared_cb)[1] == "nerve"

    # Disposing the last hook registration releases ownership for shared_cb.
    h2.dispose()
    assert manager._hook_callback_meta(shared_cb)[1] == shared_cb.__module__

    # Unloading the plugin releases remaining callback ownership entries.
    assert manager._hook_callback_meta(other_cb)[1] == "nerve"
    manager.unload("nerve")
    assert id(other_cb) not in manager._hook_callback_owners
    assert manager._hook_callback_meta(other_cb)[1] == other_cb.__module__


def test_ownership_fallback_order_for_unregistered_callbacks(manager):
    def tagged_cb(**_kwargs):
        return None

    tagged_cb._hermes_plugin_name = "legacy-attr-plugin"

    def module_cb(**_kwargs):
        return None

    class _AnonymousCallable:
        __module__ = ""

        def __call__(self, **_kwargs):
            return None

    anon_cb = _AnonymousCallable()

    assert manager._hook_callback_meta(tagged_cb)[1] == "legacy-attr-plugin"
    assert manager._hook_callback_meta(module_cb)[1] == __name__
    assert manager._hook_callback_meta(anon_cb)[1] == "unknown"
    assert manager._hook_callback_meta(anon_cb)[2] == "unknown"


def test_sync_hook_timeout_warning_emits_owner_module_budget_and_elapsed(manager, monkeypatch, caplog):
    ctx = _make_ctx(manager, "nerve")
    monkeypatch.setattr("hermes_cli.plugins._resolve_hook_callback_timeout", lambda: 30.0)

    def post_tool_call(**_kwargs):
        return None

    post_tool_call.__module__ = "hermes_plugins.nerve.hermes_nerve.nervous"
    ctx.register_hook("post_tool_call", post_tool_call)

    # Simulate bounded worker timing out with GIL delay (started at 100.0, woke at 134.2 -> elapsed=34.2s)
    clock = [100.0]
    monkeypatch.setattr(dispatch.time, "monotonic", lambda: clock[0])

    class _TimedOutEvent:
        def set(self):
            pass

        def wait(self, timeout=None):
            clock[0] = 134.2
            return False

    class _NoopThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            pass

    monkeypatch.setattr(dispatch.threading, "Event", _TimedOutEvent)
    monkeypatch.setattr(dispatch.threading, "Thread", _NoopThread)

    with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
        assert manager.invoke_hook("post_tool_call", tool_name="bash", tool_call_id="call-1") == []

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0]
    assert msg.startswith("Hook 'post_tool_call' callback post_tool_call")
    assert "plugin=nerve" in msg
    assert "module=hermes_plugins.nerve.hermes_nerve.nervous" in msg
    assert "30.0s" in msg
    assert "elapsed=34.2s" in msg


def test_sync_suppressed_and_abandoned_cap_and_start_failure_warnings_emit_owner_and_module(
    manager, monkeypatch, caplog
):
    ctx = _make_ctx(manager, "nerve")
    monkeypatch.setattr("hermes_cli.plugins._resolve_hook_callback_timeout", lambda: 0.1)

    hold = threading.Event()

    def post_tool_call(**_kwargs):
        hold.wait(timeout=5.0)

    post_tool_call.__module__ = "hermes_plugins.nerve.obs"
    ctx.register_hook("post_tool_call", post_tool_call)

    try:
        with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
            # 1st call times out and enters suppression window
            manager.invoke_hook("post_tool_call", tool_name="bash", tool_call_id="c1")
            # 2nd call hits suppression skip
            manager.invoke_hook("post_tool_call", tool_name="bash", tool_call_id="c2")

        skip_msgs = [
            r.getMessage()
            for r in caplog.records
            if "skipped after previous timeout or while still running" in r.getMessage()
        ]
        assert len(skip_msgs) == 1
        assert "Hook 'post_tool_call' callback post_tool_call" in skip_msgs[0]
        assert "plugin=nerve" in skip_msgs[0]
        assert "module=hermes_plugins.nerve.obs" in skip_msgs[0]

        # Expire suppression and hit _HOOK_MAX_ABANDONED_WORKERS cap
        caplog.clear()
        manager._hook_timeout_suppression_seconds = 0.0
        manager._hook_timeout_suppressed_until.clear()
        with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
            for idx in range(dispatch._HOOK_MAX_ABANDONED_WORKERS + 1):
                manager.invoke_hook("post_tool_call", tool_name="bash", tool_call_id=f"cap-{idx}")

        cap_msgs = [
            r.getMessage()
            for r in caplog.records
            if "abandoned worker(s) still running" in r.getMessage()
        ]
        assert cap_msgs
        assert "plugin=nerve" in cap_msgs[0]
        assert "module=hermes_plugins.nerve.obs" in cap_msgs[0]
        assert f"{dispatch._HOOK_MAX_ABANDONED_WORKERS} abandoned worker(s)" in cap_msgs[0]
    finally:
        hold.set()

    # Worker thread start RuntimeError releases running token and logs owner + module
    def start_fail_cb(**_kwargs):
        return "ok"

    start_fail_cb.__module__ = "hermes_plugins.nerve.runner"
    ctx.register_hook("pre_llm_call", start_fail_cb)

    class _BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    caplog.clear()
    monkeypatch.setattr(dispatch.threading, "Thread", _BrokenThread)
    with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
        assert manager.invoke_hook("pre_llm_call", turn_id="t-1") == []

    start_msgs = [r.getMessage() for r in caplog.records if "worker failed to start" in r.getMessage()]
    assert len(start_msgs) == 1
    assert "Hook 'pre_llm_call' callback start_fail_cb" in start_msgs[0]
    assert "plugin=nerve" in start_msgs[0]
    assert "module=hermes_plugins.nerve.runner" in start_msgs[0]
    assert "can't start new thread" in start_msgs[0]
    assert not manager._hook_running_callbacks


def test_async_hook_timeout_logs_owner_module_elapsed_and_fails_closed_for_policy(
    manager, monkeypatch, caplog
):
    nerve_ctx = _make_ctx(manager, "nerve")
    guard_ctx = _make_ctx(manager, "guard")
    monkeypatch.setattr("hermes_cli.plugins._resolve_hook_callback_timeout", lambda: 0.1)

    async def slow_observer(**_kwargs):
        await asyncio.Event().wait()

    async def slow_guard(**_kwargs):
        await asyncio.Event().wait()

    slow_observer.__module__ = "hermes_plugins.nerve.async_obs"
    slow_guard.__module__ = "hermes_plugins.guard.policy"
    nerve_ctx.register_hook("post_tool_call", slow_observer)
    guard_ctx.register_hook("pre_tool_call", slow_guard)

    with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
        obs_res = asyncio.run(manager.ainvoke_hook("post_tool_call", tool_name="bash", tool_call_id="a1"))
        pol_res = asyncio.run(manager.ainvoke_hook("pre_tool_call", tool_name="bash", tool_call_id="a2"))

    assert obs_res == []
    assert pol_res == [{"action": "block", "message": _PRE_TOOL_CALL_TIMEOUT_BLOCK_MESSAGE}]

    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(msgs) == 2
    assert "Hook 'post_tool_call' callback slow_observer" in msgs[0]
    assert "plugin=nerve" in msgs[0]
    assert "module=hermes_plugins.nerve.async_obs" in msgs[0]
    assert "0.1s" in msgs[0] and "elapsed=" in msgs[0]

    assert "Hook 'pre_tool_call' callback slow_guard" in msgs[1]
    assert "plugin=guard" in msgs[1]
    assert "module=hermes_plugins.guard.policy" in msgs[1]
    assert "0.1s" in msgs[1] and "elapsed=" in msgs[1]
