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


def test_finished_native_scope_invalidates_all_context_copies():
    from contextvars import copy_context
    from agent.delegation_context import current_task_intent, native_task_intent

    with native_task_intent(actor_session_id="child", parent_session_id="parent", goal="Inspect.", context=None):
        observer = copy_context()
        assert observer.run(current_task_intent, "child", "parent") is not None
    assert observer.run(current_task_intent, "child", "parent") is None


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


@pytest.mark.parametrize("end", ["replace", "revoke", "stop"])
def test_observers_and_retained_handoff_do_not_survive_invalidation(end):
    from contextvars import copy_context
    from agent import delegation_context as dc
    from tools.delegate_tool_child_run import _signal_child_stop

    args: dict[str, Any] = dict(actor_session_id="child", parent_session_id="parent", goal="Write proposal.md.", context=None)
    with dc.native_task_intent(**args) as old:
        assert old is not None
        handoff = dc._ResumeIntent(old)
        observer = copy_context()
        if end == "replace":
            with dc.native_task_intent(**args) as new:
                assert new is not None and new.assignment_id != old.assignment_id
                assert observer.run(dc.current_task_intent, "child", "parent") is None
                dc.revoke_task_intent(old)
                assert dc.current_task_intent("child", "parent") is new
        elif end == "revoke":
            dc.revoke_task_intent(old)
        else:
            child = SimpleNamespace(_delegate_running_intent=old, _interrupt_requested=False)
            _signal_child_stop(child)
        assert observer.run(dc.current_task_intent, "child", "parent") is None
    with dc.native_task_intent(**args, resumed=True, handoff=handoff):
        assert dc.current_task_intent("child", "parent") is None


@pytest.mark.parametrize("stop_via", ["child-runner", "parent-hard-interrupt"])
@pytest.mark.parametrize("stop_at", ["before-start", "after-activation"])
def test_native_child_stop_fences_intent_during_startup(stop_at, stop_via, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from contextvars import copy_context
    from threading import Event, Lock

    from agent.delegation_context import current_task_intent
    from agent.interrupt_control import InterruptControlMixin
    from tools import delegate_tool_child_run as runner

    class Agent(InterruptControlMixin, SimpleNamespace):
        pass

    def make_agent(**kwargs):
        return Agent(_active_children=[], _active_children_lock=Lock(),
                     _hard_interrupt_requested=Event(), _interrupt_requested=False,
                     _execution_thread_id=None, quiet_mode=True, **kwargs)

    entered, release = Event(), Event()
    native = runner.native_task_intent
    observers = []

    @contextmanager
    def pause_after_activation(**kwargs):
        with native(**kwargs) as intent:
            observers.append(copy_context())
            entered.set()
            assert release.wait(10)
            yield intent

    if stop_via == "parent-hard-interrupt":
        parent = make_agent(session_id="parent")
        child = make_agent(session_id="child", _parent_session_id="parent", model="offline", platform="cli",
                           _persist_disabled=False, _delegate_depth=1)
        parent._active_children.append(child)

        def stop():
            parent.hard_interrupt("Stop requested")
            assert child._hard_interrupt_requested.is_set()
    else:
        parent = SimpleNamespace(session_id="parent")
        child = SimpleNamespace(session_id="child", _parent_session_id="parent", model="offline", platform="cli",
                                _persist_disabled=False, _interrupt_requested=False)

        def stop():
            runner._signal_child_stop(child)

    hook_records = []

    def converse(user_message, **kwargs):
        hook_records.append(current_task_intent("child", "parent"))
        return {"final_response": "offline"}

    child.run_conversation = converse
    run = _ChildRun(child, parent, 0, "Write proposal.md.", None, None)
    if stop_at == "before-start":
        stop()
        result, error, deferred = run.await_child()
    else:
        monkeypatch.setattr(runner, "native_task_intent", pause_after_activation)
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(run.await_child)
            try:
                assert entered.wait(10)
                assert observers[0].run(current_task_intent, "child", "parent") is not None
                stop()
                assert observers[0].run(current_task_intent, "child", "parent") is None
            finally:
                release.set()
            result, error, deferred = worker.result(timeout=10)
    assert result == {"final_response": "offline"} and error is None and not deferred
    assert hook_records == [None]


@pytest.mark.parametrize("case", [
    "complete", "missing-required", "missing-reference", "different-project", "different-digest",
    "stale-missing-file", "stale-size", "corrupt-child-config",
    "wrong-parent", "forged-model-selection", "internal-event", "unauthorized-event", "closed-turn",
    "different-child-workspace", "unknown-child-workspace", "one-sided-optional",
    "one-sided-reference", "stale-file-identity",
])
def test_native_proposal_fixture_selects_only_live_common_catalog(case, tmp_path, monkeypatch, captured_hook):
    from contextvars import copy_context
    from copy import deepcopy
    from json import dumps

    from agent.prerequisite_workflow import (
        _original_delivery_scope, authenticate_proposal_command, note_proposal_command, proposal_turn,
    )
    from gateway.platforms.event import MessageEvent
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tests.tools.proposal_catalog_fixture import CHANGE, CONTEXT, GOAL, profile_config, proposal_entry

    root = tmp_path / "planning"
    parent_entry = proposal_entry(root)
    child_entry = deepcopy(parent_entry)
    child_entry.update(operations=18, seconds=120)
    if case == "missing-required":
        del child_entry["resources"]["governance/AGENTS.md"]
    elif case == "missing-reference":
        del child_entry["resources"]["reference/verification-before-claim/references/completion-gate.md"]
    elif case == "different-project":
        child_entry["planning_home"] = str(tmp_path / "other-planning")
    elif case == "different-digest":
        child_entry["resources"]["governance/AGENTS.md"]["sha256"] = "0" * 64

    if case in {"one-sided-optional", "one-sided-reference"}:
        relative = "REQUIREMENTS.md" if case == "one-sided-optional" else "skills/codebase-design/references/extra.md"
        extra = root / relative
        extra.parent.mkdir(parents=True, exist_ok=True)
        content = b"Extra fixture document."
        extra.write_bytes(content)
        stat = extra.stat()
        key = ("project/requirements" if case == "one-sided-optional"
               else "reference/codebase-design/references/extra.md")
        parent_entry["resources"][key] = {"path": relative if case == "one-sided-optional" else str(extra),
                                         "device": stat.st_dev, "inode": stat.st_ino, "size": len(content),
                                         "max_file_bytes": 512, "sha256": sha256(content).hexdigest()}

    governance = root / "rules" / "AGENTS.md"
    if case == "stale-file-identity":
        replacement = governance.with_suffix(".replacement")
        replacement.write_bytes(governance.read_bytes())
        assert replacement.stat().st_ino != governance.stat().st_ino
        replacement.replace(governance)
    elif case == "stale-missing-file":
        governance.unlink()
    elif case == "stale-size":
        governance.write_bytes(b"Changed after pinning")

    parent_home, child_home = tmp_path / "parent", tmp_path / "child"
    for home, entry in ((parent_home, parent_entry), (child_home, child_entry)):
        home.mkdir()
        (home / "config.yaml").write_text(dumps(profile_config(entry)), encoding="utf-8")
    if case == "corrupt-child-config":
        from hermes_cli.config import load_config_readonly
        from hermes_cli.config_effective import load_user_config_effective

        token = set_hermes_home_override(str(child_home))
        try:
            assert load_user_config_effective(fail_closed=True)["prerequisite_catalog"]
            assert load_config_readonly()["prerequisite_catalog"]
            (child_home / "config.yaml").write_text("prerequisite_catalog: [\n", encoding="utf-8")
            assert load_config_readonly()["prerequisite_catalog"]  # Ordinary settings retain fallback.
        finally:
            reset_hermes_home_override(token)
    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda _: None)
    event = MessageEvent(text=f"/openspec-propose {CHANGE}", user_id="test-user", internal=case == "internal-event",
                         allow_gateway_control=case != "unauthorized-event")
    event.metadata["proposal_selection"] = child_entry  # Untrusted event metadata is never consulted.
    parent_id = "other-parent" if case == "wrong-parent" else "parent"
    child = SimpleNamespace(session_id="child-id", _parent_session_id=parent_id, model="offline", platform="cli",
                            _persist_disabled=False, _delegate_context=CONTEXT, _delegate_profile_home=str(child_home))

    def converse(user_message, **kwargs):
        _collect_pre_llm_call_context(child, effective_task_id="task", turn_id="turn",
                                      original_user_message=user_message, messages=[], conversation_history=None)
        return {"final_response": "offline"}

    child.run_conversation = converse
    from tools.delegate_tool_routes import child_profile_scope
    from tools.terminal_tool import clear_session_cwd, record_session_cwd

    run = _ChildRun(child, SimpleNamespace(session_id=parent_id), 0, GOAL, None, None, child_task_id="proposal-child")
    child_workspace = root
    if case == "different-child-workspace":
        child_workspace = tmp_path / "other-workspace"
        child_workspace.mkdir()
    home_token = set_hermes_home_override(str(parent_home))
    try:
        with child_profile_scope(child):
            if case != "unknown-child-workspace":
                record_session_cwd(run.child_task_id, str(child_workspace))
        if case != "forged-model-selection":
            with _original_delivery_scope(event):
                authenticate_proposal_command(event, event.text)
                skill_path = parent_entry["resources"]["skill/openspec-propose"]["path"]
                note_proposal_command(event, "route", "openspec-propose", CHANGE, skill_path)
        if case == "closed-turn":
            with proposal_turn(event, "route", "parent"):
                deferred_context = copy_context()
            result, error, deferred = deferred_context.run(run.await_child)
        else:
            with proposal_turn(event, "route", "parent"):
                result, error, deferred = run.await_child()
    finally:
        try:
            with child_profile_scope(child):
                clear_session_cwd(run.child_task_id)
        finally:
            reset_hermes_home_override(home_token)

    assert result == {"final_response": "offline"} and error is None and not deferred
    record = captured_hook[0]["task_intent"]
    assert record is not None and record.goal_text == GOAL
    selection = record.proposal_selection
    if case not in {"complete", "one-sided-optional"}:
        assert selection is None
        return
    assert selection is not None
    assert (selection.change, selection.planning_home, selection.operations, selection.seconds) == (
        CHANGE, str(root), 18, 120,
    )
    assert set(selection.absent_optional) == {"project/requirements", "project/architecture"}
    pins = {pin.key: pin for pin in selection.resources}
    expected = {key: row for key, row in parent_entry["resources"].items() if row is not None}
    if case == "one-sided-optional":
        assert expected.pop("project/requirements") is not None
        assert "project/requirements" not in pins
    assert pins.keys() == expected.keys()
    for key, row in expected.items():
        assert pins[key].path == str(root / row["path"])
        assert pins[key].sha256 == row["sha256"]


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
