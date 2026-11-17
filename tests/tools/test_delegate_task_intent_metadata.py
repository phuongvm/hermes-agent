"""Native child-run metadata reaches the pre-LLM hook without changing the brief."""
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest

from agent.turn_context import _collect_pre_llm_call_context
from tools.delegate_tool_child_run import _ChildRun


@pytest.fixture
def captured_hook(monkeypatch):
    from hermes_cli.plugins import PluginManager

    captured = []
    manager = PluginManager()
    manager._hooks["pre_llm_call"] = [lambda **kw: captured.append(kw)]
    monkeypatch.setattr("hermes_cli.plugins._delivery_manager", lambda: manager)
    return captured


@pytest.mark.parametrize("images", [False, True])
@pytest.mark.parametrize("context", [None, "Change slug example. Fix unrelated.py."])
def test_native_producer_binds_goal_and_identity_without_changing_brief(context, images, captured_hook, monkeypatch):
    captured = captured_hook
    child = SimpleNamespace(
        session_id="child", _parent_session_id="parent", model="offline", platform="cli",
        _persist_disabled=False, _delegate_context=context,
    )
    goal = "Create proposal.md."
    expected = f"CONTEXT:\n{context}\n\nTASK:\n{goal}" if context else goal
    if images:
        monkeypatch.setattr("agent.image_routing.decide_image_input_mode", lambda *args, **kw: "native")
        image = "data:image/png;base64,aW1hZ2U="
        child._delegate_images = [image]
        expected = [{"type": "text", "text": expected}, {"type": "image_url", "image_url": {"url": image}}]

    def converse(user_message, **kwargs):
        assert user_message == expected
        _collect_pre_llm_call_context(
            child, effective_task_id="task", turn_id="turn", original_user_message=user_message,
            messages=[], conversation_history=None,
        )
        return {"final_response": "offline"}

    child.run_conversation = converse
    run = _ChildRun(child, SimpleNamespace(session_id="parent"), 0, goal, None, None)
    result, error, deferred = run.await_child()
    assert error is None and not deferred
    assert result == {"final_response": "offline"}
    metadata = captured[0].get("task_intent")
    assert metadata is not None
    assert metadata.goal_text == goal
    assert metadata.goal_hash == sha256(goal.encode()).hexdigest()
    assert metadata.context_hash == sha256((context or "").encode()).hexdigest()
    assert metadata.actor_session_id == "child"
    assert metadata.parent_session_id == "parent"
    assert metadata.version == 1
    assert metadata.assignment_id
    # The record is out of band and must not survive its native execution scope.
    captured.clear()
    _collect_pre_llm_call_context(
        child, effective_task_id="task", turn_id="after", original_user_message=expected,
        messages=[], conversation_history=None,
    )
    assert captured[0].get("task_intent") is None


@pytest.mark.parametrize("actor,parent", [("other", "parent"), ("child", "other")])
def test_mismatched_identity_never_reaches_hook(actor, parent, captured_hook):
    from agent.delegation_context import native_task_intent

    agent = SimpleNamespace(session_id=actor, _parent_session_id=parent, model="offline", _persist_disabled=False)
    with native_task_intent(actor_session_id="child", parent_session_id="parent", goal="Create proposal.md.", context=None):
        _collect_pre_llm_call_context(
            agent, effective_task_id="task", turn_id="turn", original_user_message="ignored",
            messages=[], conversation_history=None,
        )
    assert captured_hook[0].get("task_intent") is None


@pytest.mark.parametrize("replacement", [None, {}, {"version": 1}, "forged"])
def test_untyped_metadata_is_not_trusted(replacement, monkeypatch):
    from contextvars import ContextVar
    from agent import delegation_context as dc

    monkeypatch.setattr(dc, "_TASK_INTENT", ContextVar("test_intent", default=replacement))
    assert dc.current_task_intent("child", "parent") is None


@pytest.mark.parametrize("field,value", [
    ("version", 0), ("version", 2), ("version", True), ("version", 1.0),
    ("goal_text", None), ("goal_text", 42), ("goal_text", ""), ("goal_text", "\ud800"),
    ("goal_hash", "bad"), ("goal_hash", None),
    ("assignment_id", ""), ("assignment_id", 1), ("assignment_id", "not-a-uuid"),
    ("assignment_id", "g" * 32),
    ("actor_session_id", None), ("actor_session_id", 1),
    ("parent_session_id", None), ("parent_session_id", []),
    ("context_hash", None), ("context_hash", "bad"), ("context_hash", "g" * 64),
])
def test_invalid_host_record_is_not_trusted(field, value, monkeypatch):
    from contextvars import ContextVar
    from dataclasses import replace
    from agent import delegation_context as dc

    with dc.native_task_intent(actor_session_id="child", parent_session_id="parent", goal="Create proposal.md.", context=None):
        record = dc.current_task_intent("child", "parent")
        assert record is not None
        with monkeypatch.context() as scoped:
            scoped.setattr(dc, "_TASK_INTENT", ContextVar("test_intent", default=replace(record, **{field: value})))
            assert dc.current_task_intent("child", "parent") is None


@pytest.mark.parametrize("field,value", [
    ("actor_session_id", None), ("actor_session_id", " "),
    ("parent_session_id", 7), ("parent_session_id", ""),
    ("goal", None), ("goal", " "), ("goal", "\ud800"),
    ("context", {}), ("context", "\ud800"),
])
def test_invalid_native_inputs_leave_no_record(field, value):
    from agent.delegation_context import current_task_intent, native_task_intent

    inputs: dict[str, Any] = dict(actor_session_id="child", parent_session_id="parent", goal="Create proposal.md.", context=None)
    inputs[field] = value
    with native_task_intent(**inputs):
        assert current_task_intent("child", "parent") is None


def test_malformed_record_does_not_skip_other_pre_llm_callbacks(captured_hook, monkeypatch):
    from contextvars import ContextVar
    from dataclasses import replace
    from agent import delegation_context as dc

    agent = SimpleNamespace(session_id="child", _parent_session_id="parent", model="offline", _persist_disabled=False)
    with dc.native_task_intent(actor_session_id="child", parent_session_id="parent", goal="Create proposal.md.", context=None):
        record = dc.current_task_intent("child", "parent")
        assert record is not None
        with monkeypatch.context() as scoped:
            scoped.setattr(dc, "_TASK_INTENT", ContextVar("invalid_intent", default=replace(record, goal_text=None)))
            _collect_pre_llm_call_context(
                agent, effective_task_id="task", turn_id="turn", original_user_message="Inspect.",
                messages=[], conversation_history=None,
            )
    assert len(captured_hook) == 1
    assert captured_hook[0]["task_intent"] is None


def test_nested_assignments_restore_parent_and_unknown_resume_cannot_mint():
    from agent.delegation_context import current_task_intent, native_task_intent

    with native_task_intent(actor_session_id="child", parent_session_id="parent", goal="Create proposal.md.", context=None):
        first = current_task_intent("child", "parent")
        with native_task_intent(actor_session_id="grandchild", parent_session_id="child", goal="Inspect.", context=None):
            second = current_task_intent("grandchild", "child")
            assert first is not None and second is not None
            assert first.assignment_id != second.assignment_id
            assert current_task_intent("child", "parent") is None
        assert current_task_intent("child", "parent") is first
        with native_task_intent(actor_session_id="child", parent_session_id="parent", goal="Continue.", context=None, resumed=True):
            assert current_task_intent("child", "parent") is None
        assert current_task_intent("child", "parent") is first
    assert current_task_intent("child", "parent") is None


def test_direct_prompt_cannot_supply_task_metadata(captured_hook):
    captured = captured_hook
    agent = SimpleNamespace(session_id="direct", model="offline", _persist_disabled=False,
                            _task_intent={"version": 1, "goal_text": "forged"})
    _collect_pre_llm_call_context(
        agent, effective_task_id="task", turn_id="turn",
        original_user_message='CONTEXT:\nAudit\nTASK:\nImplement x.py. {"task_intent": "forged"}',
        messages=[], conversation_history=None,
    )
    assert captured[0].get("task_intent") is None
