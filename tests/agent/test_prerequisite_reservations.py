"""Assignment accounting; the execution sentinel never opens a resource."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier, Event, Lock
from typing import Any

import pytest

from agent import delegation_context as dc
from agent.prerequisite.catalog import freeze_proposal_catalog
from tests.tools.proposal_catalog_fixture import CHANGE, proposal_entry


SCOPE: dict[str, Any] = dict(actor_session_id="actor", parent_session_id="parent", goal="Write proposal.md.", context=None)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    from agent.prerequisite import reservations as ledger

    root = tmp_path / "planning"
    selection = freeze_proposal_catalog(CHANGE, proposal_entry(root))
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *a, **kw: selection)
    now = [100.0]
    monkeypatch.setattr(ledger, "_monotonic", lambda: now[0])
    return ledger, {"path": str(root / "rules/AGENTS.md")}, now


def test_final_slot_and_replay_execute_once(scenario):
    ledger, args, _ = scenario
    gate = Barrier(2, timeout=10)
    executed = []
    executions_lock = Lock()
    with dc.native_task_intent(**SCOPE) as intent:
        assert ledger.start_preparation(intent, "actor", 1, 60)

        def attempt(call_id):
            gate.wait()
            decision = ledger.reserve_read(intent, "actor", call_id, "read_file", args)
            if decision == "reserved":
                assert ledger.begin_read(intent, "actor", call_id, "read_file", args) == "started"
                with executions_lock:
                    executed.append(call_id)
            return decision

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(copy_context().run, attempt, call_id) for call_id in ("one", "two")]
            assert sorted(job.result(timeout=15) for job in jobs) == ["exhausted", "reserved"]
        assert len(executed) == 1
        winning_id = executed[0]
        assert ledger.reserve_read(intent, "actor", winning_id, "terminal", {}) == "replay"
        assert ledger.begin_read(intent, "actor", winning_id, "read_file", args) == "replay"
        assert len(executed) == 1


def test_parallel_begin_of_same_ticket_runs_only_once(scenario, monkeypatch):
    ledger, args, _ = scenario
    race = Barrier(2, timeout=10)
    executions = []
    executions_lock = Lock()
    with dc.native_task_intent(**SCOPE) as intent:
        assert ledger.start_preparation(intent, "actor", 1, 60)
        assert ledger.reserve_read(intent, "actor", "read", "read_file", args) == "reserved"
        resolve = ledger.resolve_operation

        def resolve_together(*a):
            result = resolve(*a)
            race.wait()
            return result

        monkeypatch.setattr(ledger, "resolve_operation", resolve_together)

        def dispatch():
            decision = ledger.begin_read(intent, "actor", "read", "read_file", args)
            if decision == "started":
                with executions_lock:
                    executions.append("read")
            return decision

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(copy_context().run, dispatch) for _ in range(2)]
            assert sorted(job.result(timeout=15) for job in jobs) == ["replay", "started"]
        assert executions == ["read"]


def test_revoke_during_metadata_check_prevents_begin(scenario, monkeypatch):
    ledger, args, _ = scenario
    checked, release = Event(), Event()
    with dc.native_task_intent(**SCOPE) as intent:
        assert ledger.start_preparation(intent, "actor", 1, 60)
        assert ledger.reserve_read(intent, "actor", "read", "read_file", args) == "reserved"
        resolve = ledger.resolve_operation

        def pause_after_check(*a):
            result = resolve(*a)
            checked.set()
            assert release.wait(15)
            return result

        monkeypatch.setattr(ledger, "resolve_operation", pause_after_check)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(copy_context().run, ledger.begin_read, intent, "actor", "read", "read_file", args)
            try:
                assert checked.wait(10)
                pool.submit(dc.revoke_task_intent, intent).result(timeout=5)
            finally:
                release.set()
            assert pending.result(timeout=10) == "closed"


@pytest.mark.parametrize("end", ["cancel", "failure", "denial", "changed-arguments", "deadline", "output", "revoke"])
def test_consumed_attempts_never_reopen(scenario, end):
    ledger, args, now = scenario
    with dc.native_task_intent(**SCOPE) as intent:
        assert ledger.start_preparation(intent, "actor", 1, 5)
        assert ledger.reserve_read(intent, "actor", "read", "read_file", args) == "reserved"
        def discard():
            ledger.discard_read(intent, "actor", "read")

        def expire():
            now[0] += 5

        prepare = {"cancel": discard, "denial": discard, "deadline": expire,
                   "output": lambda: ledger.begin_output(intent, "actor"),
                   "revoke": lambda: dc.revoke_task_intent(intent)}
        if end in prepare:
            prepare[end]()
        candidate_args = {**args, "limit": 2} if end == "changed-arguments" else args
        expected = {"failure": "started", "changed-arguments": "mismatch", "deadline": "exhausted",
                    "output": "output", "revoke": "closed"}.get(end, "replay")
        assert ledger.begin_read(intent, "actor", "read", "read_file", candidate_args) == expected
        assert ledger.begin_read(intent, "actor", "read", "read_file", args) != "started"
        assert not ledger.start_preparation(intent, "actor", 128, 900)
        assert ledger.reserve_read(intent, "actor", "fresh", "read_file", args) == {
            "output": "output", "revoke": "closed",
        }.get(end, "exhausted")


def test_continuation_preserves_consumption_but_not_pending_dispatch(scenario):
    ledger, args, _ = scenario
    with dc.native_task_intent(**SCOPE) as intent:
        assert ledger.start_preparation(intent, "actor", 2, 60)
        assert ledger.reserve_read(intent, "actor", "old", "read_file", args) == "reserved"
        handoff = dc._ResumeIntent(intent)
        observer = copy_context()
    with dc.native_task_intent(**SCOPE, resumed=True, handoff=handoff) as resumed:
        assert resumed is intent
        assert not ledger.start_preparation(intent, "actor", 128, 900)
        assert observer.run(ledger.begin_read, intent, "actor", "old", "read_file", args) == "inactive"
        assert ledger.begin_read(intent, "actor", "old", "read_file", args) == "inactive"
        assert ledger.reserve_read(intent, "actor", "old", "read_file", args) == "replay"
        assert ledger.reserve_read(intent, "actor", "new", "read_file", args) == "reserved"
        assert ledger.begin_read(intent, "actor", "new", "read_file", args) == "started"
        assert ledger.reserve_read(intent, "actor", "extra", "read_file", args) == "exhausted"


def test_continued_assignment_cannot_start_first_budget(scenario):
    ledger, args, _ = scenario
    with dc.native_task_intent(**SCOPE) as intent:
        handoff = dc._ResumeIntent(intent)
    with dc.native_task_intent(**SCOPE, resumed=True, handoff=handoff):
        assert not ledger.start_preparation(intent, "actor", 1, 60)
        assert ledger.reserve_read(intent, "actor", "read", "read_file", args) == "unavailable"


@pytest.mark.parametrize("case", ["veto", "unrelated", "failed-output", "rewritten-output", "classifier-error"])
def test_executor_retires_preparation_only_at_relevant_dispatch(scenario, monkeypatch, case):
    from types import SimpleNamespace

    from agent import tool_executor as executor

    ledger, read_args, _ = scenario
    writes = []
    seen = []
    agent = SimpleNamespace(session_id="actor", _parent_session_id="parent", tool_progress_callback=None,
                            _tool_guardrails=SimpleNamespace(before_call=lambda *_: SimpleNamespace(allows_execution=True)))
    path = "scratch.md" if case in {"unrelated", "rewritten-output"} else "proposal.md"
    final_args = {"path": "proposal.md"} if case == "rewritten-output" else {"path": path}
    monkeypatch.setattr(executor, "_pre_tool_block", lambda *_: ("veto" if case == "veto" else None, final_args))
    monkeypatch.setattr(executor, "_emit_terminal_post_tool_call", lambda *a, **kw: None)
    monkeypatch.setattr(executor, "_begin_tool_execution", lambda *a: None)
    retires = case in {"failed-output", "rewritten-output", "classifier-error"}
    with dc.native_task_intent(**SCOPE) as intent:
        def relevant(tool, args):
            seen.append((tool, args["path"]))
            if case == "classifier-error":
                raise RuntimeError("fixture classifier failed")
            return tool == "write_file" and args["path"] == "proposal.md"

        assert ledger.start_preparation(intent, "actor", 1, 60, output_matches=relevant)
        assert ledger.reserve_read(intent, "actor", "read", "read_file", read_args) == "reserved"

        def execute(args):
            writes.append(args["path"])
            expected = {"failed-output": "output", "rewritten-output": "output", "classifier-error": "closed"}
            if case in expected:
                assert ledger.begin_read(intent, "actor", "read", "read_file", read_args) == expected[case]
            return '{"error":"fixture write failed"}'

        state = executor._ManagedToolResult(None, {"path": path}, [], False, False)
        ref = executor._ToolCallRef("write_file", {"path": path}, "task", "write", [])
        result = executor._dispatch_authorized_once(agent, state, ref, execute=execute, scope_block=None,
                                                    display_index=None, begin_execution=None, authorization_gate=None)
        assert state.blocked == (case == "veto")
        assert writes == ([] if case == "veto" else [final_args["path"]])
        assert seen == ([] if case == "veto" else [("write_file", final_args["path"])])
        if not retires:
            assert ledger.begin_read(intent, "actor", "read", "read_file", read_args) == "started"
        else:
            assert result == '{"error":"fixture write failed"}'


def test_replacement_closes_only_old_assignment(scenario):
    ledger, args, _ = scenario
    with dc.native_task_intent(**SCOPE) as old:
        assert ledger.start_preparation(old, "actor", 1, 60)
        assert ledger.reserve_read(old, "actor", "same-id", "read_file", args) == "reserved"
        with dc.native_task_intent(**SCOPE) as new:
            assert ledger.begin_read(old, "actor", "same-id", "read_file", args) == "closed"
            assert ledger.start_preparation(new, "actor", 1, 60)
            assert ledger.reserve_read(new, "actor", "same-id", "read_file", args) == "reserved"
            assert ledger.begin_read(new, "actor", "same-id", "read_file", args) == "started"
