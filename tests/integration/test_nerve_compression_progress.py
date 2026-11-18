"""Cross-worktree regression: actual Hermes compaction must not renew Nerve's read budget."""
import importlib.util
import os
from contextlib import closing
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from agent.delegation_context import current_task_intent, native_task_intent
from agent.turn_context import _collect_pre_llm_call_context
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest, get_pre_tool_call_directive
from hermes_state import SessionDB
from run_agent import AIAgent
from tests.agent.test_compression_attempt_effect import _compressor


@pytest.fixture
def paired_progress(tmp_path, monkeypatch):
    root = os.environ.get("NERVE_TASK_INTENT_WORKTREE")
    if not root:
        pytest.skip("set NERVE_TASK_INTENT_WORKTREE for the paired plugin test")
    assert root is not None
    source = Path(root).resolve()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for key in ("NERVOUS_EVENTS", "OUTCOMES", "POLICY_MEMORY"):
        monkeypatch.setenv("HERMES_NERVE_" + key, str(tmp_path / (key + ".jsonl")))
    spec = importlib.util.spec_from_file_location("paired_rotation_nerve", source / "__init__.py",
                                                 submodule_search_locations=[str(source)])
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plugin)
    spec.loader.exec_module(plugin)
    nervous = plugin.nervous
    assert Path(nervous.__file__).resolve() == source / "hermes_nerve" / "nervous.py"
    system = nervous.NervousSystem(engine_factory=lambda: pytest.fail("unexpected provider call"))
    monkeypatch.setattr(system, "_do_assessment", lambda payload: None)
    monkeypatch.setattr(nervous, "_default", system)
    manager = PluginManager()
    ctx = PluginContext(PluginManifest(name="nerve", source="user"), manager)
    for hook in ("pre_llm_call", "post_llm_call", "pre_tool_call", "post_tool_call"):
        ctx.register_hook(hook, getattr(nervous, hook))
    monkeypatch.setattr("hermes_cli.plugins._delivery_manager", lambda: manager)

    system.configure(enabled=True, admission_enabled=False, mode="correct_next", local_learning=False,
                     policy_memory_enabled=False)
    try:
        yield system, manager
    finally:
        system.configure(enabled=False)


def test_first_read_after_compression_is_blocked_for_same_assignment(tmp_path, paired_progress):
    system, manager = paired_progress
    goal = "Write proposal.md."
    parent, old = "assignment-parent", "initial-segment"
    notes = tmp_path / "notes.md"
    notes.write_text("context " * 200, encoding="utf-8")
    args = {"path": str(notes)}
    history = [{"role": "user", "content": goal}]
    with closing(SessionDB(db_path=tmp_path / "sessions.db")) as db:
        db.create_session(parent, "cli")
        agent = AIAgent(api_key="test-key", base_url="http://127.0.0.1:1/v1", model="test/main-model",
                        quiet_mode=True, session_db=db, session_id=old, parent_session_id=parent,
                        skip_context_files=True, skip_memory=True)
        agent.context_compressor = _compressor()
        agent.compression_in_place = False
        agent._compression_feasibility_checked = True
        with native_task_intent(actor_session_id=old, parent_session_id=parent, goal=goal, context=None) as intent:
            assert intent is not None
            _collect_pre_llm_call_context(agent, effective_task_id="task", turn_id="before", original_user_message=goal,
                                          messages=history, conversation_history=None)
            for i in range(12):
                ids = dict(session_id=old, turn_id="before", tool_call_id=f"read-{i}")
                assert get_pre_tool_call_directive("read_file", args, **ids) == (None, None)
                text = notes.read_text(encoding="utf-8")
                manager.invoke_hook("post_tool_call", tool_name="read_file", args=args, status="ok", result=text, **ids)
                history.extend([{"role": "assistant", "content": f"Notes {i}: {text}"},
                                {"role": "user", "content": "Continue."}])
            assert system.status(turn_id="before")["tool_actions"] == 12
            manager.invoke_hook("post_llm_call", session_id=old, turn_id="before", assistant_response="Working.")

            before_len = len(history)
            with patch.object(agent.context_compressor, "_generate_summary", return_value="Notes reviewed.") as summary:
                compacted, _ = agent._compress_context(history, "system", approx_tokens=80_000)
            summary.assert_called()
            new = agent.session_id
            assert new != old
            row = db.get_session(new)
            assert row is not None and row["parent_session_id"] == old
            assert len(compacted) < before_len
            assert current_task_intent(old, parent) is None
            assert current_task_intent(new, parent) is intent

            _collect_pre_llm_call_context(agent, effective_task_id="task", turn_id="after", original_user_message="Continue.",
                                          messages=compacted, conversation_history=compacted)
            state = system.status(turn_id="after")
            assert (state["session_id"], state["task_intent_disposition"], state["tool_actions"]) == (new, "trusted", 12)
            action, reason = get_pre_tool_call_directive("read_file", args, session_id=new, turn_id="after",
                                                       tool_call_id="first-read-after-rotation")
            assert action == "block" and reason and "task-relevant implementation" in reason
            assert system.status(turn_id="after")["tool_actions"] == 12


def test_db_only_continue_uses_legacy_guard(paired_progress, tmp_path):
    """A saved transcript can resume work but cannot resume a trusted grant."""
    import json
    from tests.tools.test_delegate_continue import _completion, _parent, _ScriptedModel, _spawn
    from tests.tools.test_delegate_resumable import _forget_process_state
    from tools.delegate_tool import delegate_task

    system, manager = paired_progress
    model = _ScriptedModel(first_complete=False)
    goal = "Write proposal.md."
    observations = []

    def build(**kw):
        child = model(**kw)
        child._persist_disabled = False
        converse = child.run_conversation.side_effect

        def run(user_message, conversation_history=None, **rest):
            result = converse(user_message, conversation_history=conversation_history, **rest)
            turn = "resumed" if conversation_history else "initial"
            ids = dict(session_id=child.session_id, turn_id=turn)
            _collect_pre_llm_call_context(child, effective_task_id="task", turn_id=turn,
                                          original_user_message=user_message, messages=conversation_history or [],
                                          conversation_history=conversation_history)
            initial = system.status(**ids)
            disposition = initial["task_intent_disposition"]
            assert initial["tool_actions"] == 0  # Unknown resume uses a fresh legacy turn.
            args = {"path": "notes.md"}
            for i in range(12):
                assert get_pre_tool_call_directive("read_file", args, tool_call_id=f"{turn}-{i}", **ids) == (None, None)
                manager.invoke_hook("post_tool_call", tool_name="read_file", args=args, status="ok",
                                    result="notes", tool_call_id=f"{turn}-{i}", **ids)
            observations.append((disposition, system.status(**ids)["tool_actions"],
                                 get_pre_tool_call_directive("read_file", args, **ids)))
            manager.invoke_hook("post_llm_call", assistant_response="Working.", **ids)
            return result

        child.run_conversation.side_effect = run
        return child

    with closing(SessionDB(db_path=tmp_path / "resume.db")) as db, patch("run_agent.AIAgent", side_effect=build):
        parent = _parent(db=db)
        sid, _ = _spawn(parent, goal)
        _forget_process_state()
        out = json.loads(delegate_task(action="continue", subagent_id=sid, message=goal,
                                       background=True, parent_agent=parent))
        assert out.get("status") == "dispatched", out
        _completion(out["delegation_id"])
    first, resumed = model.turns
    assert first["intent"] is not None and resumed["intent"] is None
    assert resumed["session_id"] == first["session_id"]
    assert any(goal in msg["content"] for msg in resumed["history"])
    assert [row[:2] for row in observations] == [("trusted", 12), ("missing", 12)]
    for _, _, (action, message) in observations:
        assert action == "block" and message and "task-relevant implementation" in message
