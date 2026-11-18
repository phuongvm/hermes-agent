"""Offline Discord command → actual skill render → Gateway turn → native child hook."""
import asyncio
from hashlib import sha256
from json import dumps
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.waiting import async_wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit,case_mismatch", [
    pytest.param(True, False, id="original-command"),
    pytest.param(False, False, id="plain-text"),
    pytest.param(True, True, id="skill-case-mismatch", marks=pytest.mark.platforms("windows")),
])
async def test_gateway_proposal_command_reaches_native_child_hook(tmp_path, monkeypatch, caplog, explicit, case_mismatch):
    from agent.turn_context import _collect_pre_llm_call_context
    from gateway.platforms.event import MessageEvent, MessageType
    from hermes_cli.plugins import PluginManager
    from hermes_constants import get_hermes_home
    from tests.gateway.test_messaging_slash_command_fifo import _owner
    from tests.tools.proposal_catalog_fixture import CHANGE, CONTEXT, GOAL, profile_config, proposal_entry
    from tools import skills_tool
    from tools.delegate_tool_child_run import _ChildRun
    from tools.delegate_tool_routes import child_profile_scope
    from tools.terminal_tool import clear_session_cwd, record_session_cwd

    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    pin = entry["resources"]["skill/openspec-propose"]
    skill = Path(pin["path"])
    marker = "Offline proposal integration marker."
    data = (f"---\nname: openspec-propose\ndescription: Offline test skill\n---\n\n{marker}\n").encode()
    skill.write_bytes(data)
    pin.update(size=len(data), sha256=sha256(data).hexdigest())
    if case_mismatch:
        pin["path"] = str(planning / "Skills/openspec-propose/SKILL.md")
        caplog.set_level("INFO", logger="agent.prerequisite_workflow")
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", planning / "skills")
    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda _: None)
    config = profile_config(entry)
    for home in (get_hermes_home(), tmp_path / "child-profile"):
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(dumps(config), encoding="utf-8")

    captured = []
    manager = PluginManager()
    manager._hooks["pre_llm_call"] = [lambda **kw: captured.append(kw["task_intent"])]
    monkeypatch.setattr("hermes_cli.plugins._delivery_manager", lambda: manager)
    runner, adapter, sent = _owner(monkeypatch)
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id="42", chat_type="dm", user_id="42")
    seen = []

    async def offline_model_turn(*, message, session_id, **_):
        seen.append(message)
        child = SimpleNamespace(session_id="offline-child", _parent_session_id=session_id,
                                model="offline", platform="cli", _persist_disabled=False,
                                _delegate_context=CONTEXT, _delegate_profile_home=str(tmp_path / "child-profile"))

        def converse(user_message, **kwargs):
            _collect_pre_llm_call_context(child, effective_task_id=kwargs["task_id"], turn_id="offline-turn",
                                          original_user_message=user_message, messages=[], conversation_history=None)
            return {"final_response": "offline child"}

        child.run_conversation = converse
        parent = SimpleNamespace(session_id=session_id, _current_task_id="offline-parent-task")
        record_session_cwd(parent._current_task_id, str(planning))
        run = _ChildRun(child, parent, 0, GOAL, None, None)
        try:
            run.seed_workspace()
            result, failure, deferred = await asyncio.to_thread(run.await_child)
        finally:
            with child_profile_scope(child):
                clear_session_cwd(run.child_task_id)
            clear_session_cwd(parent._current_task_id)
        assert result == {"final_response": "offline child"} and failure is None and not deferred
        return {"final_response": "offline parent", "messages": []}

    monkeypatch.setattr(runner, "_run_agent", offline_model_turn)
    command = f"/openspec-propose {CHANGE}"
    await adapter.handle_message(MessageEvent(
        text=command if explicit else f"Discuss the command {command}", source=source,
        message_type=MessageType.COMMAND if explicit else MessageType.TEXT, message_id="proposal-integration",
    ))
    await async_wait_until(lambda: any("offline parent" in text for text in sent),
                           what="offline Gateway response")
    assert len(seen) == len(captured) == 1
    assert (marker in seen[0]) is explicit
    record = captured[0]
    assert record.goal_text == GOAL
    assert record.context_hash == sha256(CONTEXT.encode()).hexdigest()
    selection = record.proposal_selection
    if not explicit or case_mismatch:
        assert selection is None
        if case_mismatch:
            assert "PREREQUISITE_SELECTION_UNAVAILABLE: resolved-skill-mismatch" in caplog.messages
        return
    assert selection is not None
    assert (selection.change, selection.planning_home) == (CHANGE, str(planning))
    # Independently enumerated from D3, the boot protocol, and the planning/doc rules.
    assert {resource.key for resource in selection.resources} == {
        "governance/AGENTS.md",
        "governance/shared/startup-rules.md",
        "governance/shared/safety.md",
        "governance/skills/task-verification-mandate.md",
        "governance/shared/rules-index.md",
        "governance/user/communication-language.md",
        "governance/shared/lessons-digest.md",
        "governance/skills/anti-deception-instruction.md",
        "governance/skills/compliance-enforcer.md",
        "governance/workflow/plan-execution-standards.md",
        "governance/guides/ai-native-documentation.md",
        "skill/hermes-boot-protocol",
        "skill/verification-before-claim",
        "skill/openspec-propose",
        "skill/openspec-workflow-governance",
        "skill/codebase-design",
        "skill/deep-reference-protocol",
        "skill/ai-agent-collaboration",
        "skill/hermes-agent",
        "reference/verification-before-claim/references/completion-gate.md",
        "reference/openspec-workflow-governance/references/openspec-agent-workflow-guide.md",
        "project/agent-share", "project/exploration", "project/receipt",
        "baseline/nerve-crew-dod-and-role-handoff", "baseline/nerve-reflex-resilience",
        "snapshot/status", "snapshot/instructions/proposal", "snapshot/instructions/design",
        "snapshot/instructions/specs", "snapshot/instructions/tasks",
    }
    initiating_skill = next(pin for pin in selection.resources if pin.key == "skill/openspec-propose")
    assert initiating_skill.path == str(skill)
    assert initiating_skill.sha256 == sha256(data).hexdigest()
    assert set(selection.absent_optional) == {"project/requirements", "project/architecture"}
