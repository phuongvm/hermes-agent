"""No-edit completion checks are opt-in and bound to the active native assignment."""
from types import SimpleNamespace
from contextlib import nullcontext

import pytest

from agent.delegation_context import native_task_intent
from agent.turn_stop_gates import _pre_verify_nudge
from hermes_cli.plugins import PluginManager


@pytest.mark.parametrize("registered,native,paths,attempt,expected", [
    (True, True, [], 0, "pre_verify_no_edits"),
    (False, True, [], 0, None),
    (True, False, [], 0, None),
    (True, True, [], 2, None),
    (True, True, ["report.md"], 0, "pre_verify"),
])
def test_stop_routes_only_to_eligible_subscribers(monkeypatch, registered, native, paths, attempt, expected):
    manager = PluginManager()
    seen = []

    def callback(name):
        def check(**kwargs):
            seen.append((name, kwargs))
            return {"action": "continue", "message": "verify output"}
        return check

    manager._hooks["pre_verify"] = [callback("pre_verify")]
    if registered:
        manager._hooks["pre_verify_no_edits"] = [callback("pre_verify_no_edits")]
    monkeypatch.setattr("hermes_cli.plugins._delivery_manager", lambda: manager)
    monkeypatch.setattr("agent.verify_hooks.max_verify_nudges", lambda: 2)
    agent = SimpleNamespace(session_id="child", _parent_session_id="parent", _resolved_is_coding=False,
                            _turn_file_mutation_paths=set(paths))
    scope = native_task_intent(actor_session_id="child", parent_session_id="parent", goal="Write report.md.", context=None)
    with scope if native else nullcontext():
        message = _pre_verify_nudge(agent, "done", attempt)
    assert [name for name, _ in seen] == ([expected] if expected else [])
    assert message == ("verify output" if expected else None)
    if expected == "pre_verify_no_edits":
        assert seen[0][1]["assignment_id"] and seen[0][1]["changed_paths"] == []


@pytest.mark.parametrize("goal,blocked", [("Write proposal.md.", True), ("Summarize findings.", False)])
def test_registered_nerve_stop_after_failed_write(monkeypatch, tmp_path, goal, blocked):
    """Run both worktrees together with an isolated manager, no model or gateway."""
    import importlib.util
    import os
    import sys
    from pathlib import Path
    from agent.delegation_context import current_task_intent
    from hermes_cli.plugins import PluginContext, PluginManifest

    root = os.environ.get("NERVE_TASK_INTENT_WORKTREE")
    if not root:
        pytest.skip("set NERVE_TASK_INTENT_WORKTREE for the paired plugin proof")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for key in ("NERVOUS_EVENTS", "OUTCOMES", "POLICY_MEMORY"):
        monkeypatch.setenv("HERMES_NERVE_" + key, str(tmp_path / (key + ".jsonl")))
    assert root is not None
    source = Path(root).resolve()
    spec = importlib.util.spec_from_file_location("paired_nerve", source / "__init__.py", submodule_search_locations=[str(source)])
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plugin)
    spec.loader.exec_module(plugin)
    nervous = plugin.nervous
    assert Path(nervous.__file__).resolve() == source / "hermes_nerve" / "nervous.py"
    system = nervous.NervousSystem(engine_factory=lambda: pytest.fail("unexpected provider call"))
    # Remote assessment is unrelated to the local integrity decision.
    monkeypatch.setattr(system, "_do_assessment", lambda payload: None)
    monkeypatch.setattr(nervous, "_default", system)
    manager = PluginManager()
    ctx = PluginContext(PluginManifest(name="nerve", source="user"), manager)
    plugin._register_nervous_verify(ctx)
    ctx.register_hook("pre_llm_call", nervous.pre_llm_call)
    ctx.register_hook("post_tool_call", nervous.post_tool_call)
    monkeypatch.setattr("hermes_cli.plugins._delivery_manager", lambda: manager)
    monkeypatch.setattr("agent.verify_hooks.max_verify_nudges", lambda: 2)
    agent = SimpleNamespace(session_id="child", _parent_session_id="parent", _resolved_is_coding=False, _turn_file_mutation_paths=set())
    system.configure(enabled=True, admission_enabled=False, mode="correct_next", local_learning=False, policy_memory_enabled=False)
    try:
        with native_task_intent(actor_session_id="child", parent_session_id="parent", goal=goal, context=None):
            manager.invoke_hook("pre_llm_call", session_id="child", parent_session_id="parent", user_message=goal,
                                turn_id="turn", task_intent=current_task_intent("child", "parent"))
            manager.invoke_hook("post_tool_call", session_id="child", turn_id="turn", tool_name="write_file",
                                args={"path": "proposal.md"}, status="error", result="write failed")
            assert system.status(turn_id="turn")["task_intent_disposition"] == "trusted"
            assert system.status(turn_id="turn")["mutation_attempts"] == 1
            msg = _pre_verify_nudge(agent, "finished", 0)
            assert (msg is not None and "Do not claim completion" in msg) if blocked else msg is None
    finally:
        system.configure(enabled=False)
