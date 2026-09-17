import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from tui_gateway import compute_host, server
from tui_gateway.compute_host import ComputeHost, _default_workers
from tui_gateway.host_supervisor import (
    MUTATOR_ROUTE_TABLE,
    HostSupervisor,
    append_log_record,
)


def _json_lines(out: io.StringIO) -> list[dict]:
    frames = []
    for line in out.getvalue().splitlines():
        if line.strip():
            frames.append(json.loads(line))
    return frames


def _wait_for_frame(out: io.StringIO, predicate, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for frame in _json_lines(out):
            if predicate(frame):
                return frame
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for frame; saw={_json_lines(out)}")


def test_compute_host_workers_inherit_tui_pool_env_or_8(monkeypatch):
    monkeypatch.delenv("HERMES_TUI_RPC_POOL_WORKERS", raising=False)
    monkeypatch.delenv("HERMES_COMPUTE_HOST_WORKERS", raising=False)
    assert _default_workers() == 8

    monkeypatch.setenv("HERMES_TUI_RPC_POOL_WORKERS", "11")
    assert _default_workers() == 11

    # Dead-RC tombstone: malformed env falls back to 8, not the old except-branch 4.
    monkeypatch.setenv("HERMES_TUI_RPC_POOL_WORKERS", "not-an-int")
    assert _default_workers() == 8


def test_compute_host_routes_clarify_response_to_child_pending_registry(monkeypatch):
    """Interactive answers are handled in the process that owns `_pending`."""
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    sid = "host-clarify"
    server._sessions[sid] = {"history_lock": threading.Lock()}
    calls = []
    monkeypatch.setitem(
        server._methods,
        "clarify.respond",
        lambda rid, params: calls.append((rid, dict(params))) or {"result": {"status": "ok"}},
    )

    try:
        host._handle_respond(
            {
                "sid": sid,
                "request_id": "relay-response",
                "params": {"request_id": "clarify-request", "answer": "yes"},
            }
        )
        assert calls == [("relay-response", {"request_id": "clarify-request", "answer": "yes"})]
        frame = _json_lines(out)[-1]
        assert frame == {
            "type": "respond.ack",
            "sid": sid,
            "request_id": "relay-response",
            "response": {"result": {"status": "ok"}},
            "host_ns": frame["host_ns"],
        }
    finally:
        server._sessions.pop(sid, None)
        host.close()

def test_mutator_route_table_matches_prd_inventory():
    assert MUTATOR_ROUTE_TABLE == {
        "prompt.submit": "turn-path",
        "session.interrupt": "turn-path",
        "reload.mcp": "run-concurrent",
        "session.save": "run-concurrent",
        "session.compress": "idle-gated",
        "prompt.submit.truncate": "idle-gated",
        "slash.model": "idle-gated",
        "slash.personality": "idle-gated",
        "slash.prompt": "idle-gated",
        "slash.compress": "idle-gated",
        "session.reset": "idle-gated",
        "session.history.reload": "idle-gated",
        "slash.retry": "idle-gated",
    }


def test_append_log_record_single_write_lines(tmp_path):
    path = tmp_path / "agent.log"

    def writer(i: int) -> None:
        append_log_record(path, f"line-{i:03d}-" + ("x" * 2000))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 32
    assert sorted(line.split("-", 2)[1] for line in lines) == [f"{i:03d}" for i in range(32)]
    assert all(line.endswith("x" * 2000) for line in lines)


def test_supervisor_startup_reconcile_pid_reuse_guard(tmp_path, monkeypatch):
    registry = tmp_path / "dashboard-compute-host.json"
    registry.write_text(json.dumps({"host_pid": os.getpid(), "boot_id": "stale"}), encoding="utf-8")

    killed: list[int] = []
    supervisor = HostSupervisor(registry_path=registry, argv=[sys.executable, "-c", ""], autostart=False)
    monkeypatch.setattr(supervisor, "_pid_matches_compute_host", lambda _pid: False)
    monkeypatch.setattr(supervisor, "_terminate_pid", lambda pid, **_kw: killed.append(pid))

    result = supervisor.reconcile_startup_orphan()

    assert result == "pid-reuse-ignored"
    assert killed == []
    assert not registry.exists()


def _make_compress_host_session(events: list) -> dict:
    class _Agent:
        model = "host-model"
        provider = "host-provider"
        tools = []
        _cached_system_prompt = ""
        session_input_tokens = 1
        session_output_tokens = 1
        session_prompt_tokens = 1
        session_completion_tokens = 1
        session_total_tokens = 2
        session_api_calls = 1
        session_id = "rotated-id"

    agent = _Agent()
    agent.context_compressor = type("ContextEngineStub", (), {})()
    agent.context_compressor.on_session_start = (
        lambda *_args, **_kwargs: events.append("notify")
    )
    return {
        "agent": agent,
        "session_key": "before-key",
        "history": [
            {"role": "user", "content": "before"},
            {"role": "assistant", "content": "before"},
        ],
        "history_lock": threading.Lock(),
        "history_version": 2,
        "running": False,
        "manual_compression_lock": threading.Lock(),
    }


def _record_finalize(monkeypatch, events: list[str], *sids: str) -> None:
    """Give ``flush_all_sessions`` sessions and record which ones finalize."""
    keys = sids or ("s1",)
    monkeypatch.setattr(
        server,
        "_sessions",
        {sid: {"session_key": sid} for sid in keys},
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_finalize_session",
        lambda _session, end_reason="tui_close": events.append(
            f"finalize:{_session['session_key']}:{end_reason}"
        ),
        raising=False,
    )


def _register_turn(host: ComputeHost, fn, sid: str = "s1") -> None:
    """Submit a turn exactly the way ``_handle_turn_start`` does."""
    host._track_turn_future(host._executor.submit(fn), sid)


def test_shutdown_drains_in_flight_turn_before_finalizing_sessions(monkeypatch):
    events: list[str] = []
    _record_finalize(monkeypatch, events)

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    running = threading.Event()

    def _turn() -> None:
        running.set()
        time.sleep(0.3)
        events.append("turn_end")

    _register_turn(host, _turn, sid="s1")
    assert running.wait(timeout=5.0)

    host.shutdown(reason="sigterm", wait=3.0)

    # ``_finalize_session`` latches on ``session["_finalized"]``, so its single
    # run has to observe the finished turn or the tail is unpersistable. A turn
    # that *did* drain must still finalize — the live-turn skip must not
    # over-reach into sessions whose work is done.
    assert events == ["turn_end", "finalize:s1:compute_host_sigterm"]

    # The done-callback still has to remove the entry now that the container is
    # a dict: ``set.discard`` was a valid bare callback, ``dict.pop`` is not.
    deadline = time.monotonic() + 2.0
    while host._turn_futures and time.monotonic() < deadline:
        time.sleep(0.01)
    assert host._turn_futures == {}, "in-flight turns must not accumulate"


def test_shutdown_retains_a_live_turns_session_when_the_drain_deadline_expires(monkeypatch):
    wait = 1.0
    events: list[str] = []
    _record_finalize(monkeypatch, events, "live", "idle")

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    release = threading.Event()
    running = threading.Event()

    def _stuck_turn() -> None:
        running.set()
        release.wait(timeout=30.0)

    _register_turn(host, _stuck_turn, sid="live")
    assert running.wait(timeout=5.0)

    try:
        started = time.monotonic()
        host.shutdown(reason="sigterm", wait=wait)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    # ``_finalize_session`` is one-shot, and the ``shutdown(wait=False)`` that
    # follows does not join the turn. Spending "live"'s single latch mid-turn
    # would leave it permanently un-finalizable and release its active-session
    # lease out from under running work — the same lifecycle race the drain
    # exists to close, just moved past the deadline. It is retained unfinalized
    # for recovery instead. A turn outliving the window must not cost the flush
    # for anyone else, so "idle" still finalizes in the same pass.
    assert events == ["finalize:idle:compute_host_sigterm"]
    assert elapsed < wait


def test_shutdown_retains_live_sessions_within_the_stdin_closed_budget(monkeypatch):
    """The tightest real budget any caller uses is ``wait=2.0``.

    ``run_host`` finalizes through ``host.shutdown(reason="stdin_closed",
    wait=2.0)``, which is where the reserve — ``wait`` minus
    ``min(_FLUSH_RESERVE_SECS, wait / 2)`` — has the least room to work with.
    The retain-live-sessions rule must hold there without costing the flush for
    idle sessions and without pushing the call past the budget the supervisor's
    kill escalation is timed against.
    """
    wait = 2.0
    drain_budget = wait - min(compute_host._FLUSH_RESERVE_SECS, wait / 2.0)

    events: list[str] = []
    _record_finalize(monkeypatch, events, "live", "idle")

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    release = threading.Event()
    running = threading.Event()

    def _stuck_turn() -> None:
        running.set()
        release.wait(timeout=30.0)

    _register_turn(host, _stuck_turn, sid="live")
    assert running.wait(timeout=5.0)

    try:
        started = time.monotonic()
        host.shutdown(reason="stdin_closed", wait=wait)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert events == ["finalize:idle:compute_host_stdin_closed"]
    assert elapsed >= drain_budget - 1e-6, "the drain must use its full window"
    assert elapsed < wait


def test_shutdown_drain_sleep_never_overshoots_the_reserve(monkeypatch):
    """The drain's per-tick sleep must be bounded by the time left to it.

    A flat tick overshoots the drain deadline by up to one tick, eating the
    reserve held back for ``flush_all_sessions``; for a small ``wait`` that is
    the whole reserve. Asserting on the *requested* sleep totals rather than on
    wall-clock keeps this deterministic: each sleep is clamped to the remaining
    time, so the sum can never exceed the drain budget however the scheduler
    interleaves.
    """
    wait = 0.34
    drain_budget = wait - min(compute_host._FLUSH_RESERVE_SECS, wait / 2.0)

    events: list[str] = []
    _record_finalize(monkeypatch, events, "idle")

    slept: list[float] = []
    real_sleep = time.sleep

    def _recording_sleep(seconds: float) -> None:
        slept.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(compute_host.time, "sleep", _recording_sleep)

    host = ComputeHost(stdout=io.StringIO(), heartbeat_secs=0)
    release = threading.Event()
    running = threading.Event()

    def _stuck_turn() -> None:
        running.set()
        release.wait(timeout=30.0)

    _register_turn(host, _stuck_turn, sid="live")
    assert running.wait(timeout=5.0)

    try:
        host.shutdown(reason="sigterm", wait=wait)
    finally:
        release.set()

    assert events == ["finalize:idle:compute_host_sigterm"]
    assert slept, "the drain loop should have ticked at least once"
    assert sum(slept) <= drain_budget + 1e-6


def test_compute_host_admission_concurrency_and_saturation(monkeypatch):
    """Admission allows max 2 active, max 8 queued; excess returns 5019 saturation."""
    out = io.StringIO()
    host = ComputeHost(stdout=out, max_active_turns=2, max_queued_turns=8, heartbeat_secs=0)
    release = threading.Event()

    def _block_turn(*_args, **_kwargs):
        release.wait(timeout=5.0)

    monkeypatch.setattr(host, "_run_real_turn", _block_turn)

    try:
        # Submit 2 active turns (different sessions)
        host.handle_frame({"type": "turn.start", "sid": "s1", "request_id": "r1"})
        host.handle_frame({"type": "turn.start", "sid": "s2", "request_id": "r2"})

        # Submit 8 queued turns (different sessions)
        for i in range(3, 11):
            host.handle_frame({"type": "turn.start", "sid": f"s{i}", "request_id": f"r{i}"})

        # 11th turn saturates capacity (2 active + 8 queued = 10 capacity full)
        host.handle_frame({"type": "turn.start", "sid": "s11", "request_id": "r11"})

        frames = _json_lines(out)
        accepted = [f for f in frames if f["type"] == "turn.accepted"]
        errors = [f for f in frames if f["type"] == "turn.error"]

        assert len(accepted) == 10
        assert [a["queue_position"] for a in accepted[:2]] == [0, 0]
        assert [a["queue_position"] for a in accepted[2:]] == list(range(1, 9))

        assert len(errors) == 1
        err = errors[0]
        assert err["request_id"] == "r11"
        assert err["code"] == 5019
        assert err["retry_after_ms"] == 1000
        assert err["reason"] == "capacity_saturated"
        assert err["execution_state"] == "not_started"
    finally:
        release.set()
        host.close()


def test_compute_host_per_session_serialization(monkeypatch):
    """Per-session serialization allows 1 active and at most 1 queued; third turn fails."""
    out = io.StringIO()
    host = ComputeHost(stdout=out, max_active_turns=2, max_queued_turns=8, heartbeat_secs=0)
    release = threading.Event()

    def _block_turn(*_args, **_kwargs):
        release.wait(timeout=5.0)

    monkeypatch.setattr(host, "_run_real_turn", _block_turn)

    try:
        # Turn 1 for s1: active
        host.handle_frame({"type": "turn.start", "sid": "s1", "request_id": "r1"})
        # Turn 2 for s1: queued
        host.handle_frame({"type": "turn.start", "sid": "s1", "request_id": "r2"})
        # Turn 3 for s1: rejected (already 1 active + 1 queued for this session)
        host.handle_frame({"type": "turn.start", "sid": "s1", "request_id": "r3"})

        frames = _json_lines(out)
        accepted = [f for f in frames if f["type"] == "turn.accepted"]
        errors = [f for f in frames if f["type"] == "turn.error"]

        assert len(accepted) == 2
        assert accepted[0]["request_id"] == "r1" and accepted[0]["queue_position"] == 0
        assert accepted[1]["request_id"] == "r2" and accepted[1]["queue_position"] == 1

        assert len(errors) == 1
        err = errors[0]
        assert err["request_id"] == "r3"
        assert err["code"] == 5019
        assert err["retry_after_ms"] == 1000
        assert err["reason"] == "capacity_saturated"
        assert "session turn queue full" in err["message"]
    finally:
        release.set()
        host.close()


def test_compute_host_queue_timeout_expires_turn(monkeypatch):
    """Queued turn expires with queue_timeout if not started within queue_timeout_secs."""
    out = io.StringIO()
    host = ComputeHost(
        stdout=out, max_active_turns=1, max_queued_turns=2,
        queue_timeout_secs=0.05, heartbeat_secs=0
    )
    release = threading.Event()

    def _block_turn(*_args, **_kwargs):
        release.wait(timeout=5.0)

    monkeypatch.setattr(host, "_run_real_turn", _block_turn)

    try:
        # Turn 1 for s1: active
        host.handle_frame({"type": "turn.start", "sid": "s1", "request_id": "r1"})
        # Turn 2 for s2: queued
        host.handle_frame({"type": "turn.start", "sid": "s2", "request_id": "r2"})

        # Wait for queue timeout to trigger
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            frames = _json_lines(out)
            if any(f["type"] == "turn.error" and f.get("reason") == "queue_timeout" for f in frames):
                break
            time.sleep(0.02)

        frames = _json_lines(out)
        timeout_errs = [f for f in frames if f["type"] == "turn.error" and f.get("reason") == "queue_timeout"]
        assert len(timeout_errs) == 1
        assert timeout_errs[0]["request_id"] == "r2"
        assert timeout_errs[0]["execution_state"] == "not_started"
    finally:
        release.set()
        host.close()


def test_compute_host_heavy_work_ceiling_shared_with_control(monkeypatch):
    """Total heavy-work ceiling of 2 is shared between active turns and heavy controls."""
    out = io.StringIO()
    host = ComputeHost(stdout=out, max_active_turns=2, max_queued_turns=8, heartbeat_secs=0)
    release_turn = threading.Event()
    release_ctrl = threading.Event()

    def _block_turn(*_args, **_kwargs):
        release_turn.wait(timeout=5.0)

    monkeypatch.setattr(host, "_run_real_turn", _block_turn)
    # Mock heavy control
    monkeypatch.setattr(host, "_control_ack", lambda *a, **kw: release_ctrl.wait(timeout=5.0) or {})

    sid = "s-ctrl"
    session = {"history_lock": threading.Lock(), "agent": None}
    server._sessions[sid] = session

    try:
        # Start heavy control (session.compress)
        ctrl_thread = threading.Thread(
            target=host.handle_frame,
            args=({"type": "control", "route_name": "session.compress", "sid": sid, "request_id": "c1"},),
            daemon=True,
        )
        ctrl_thread.start()
        time.sleep(0.05)

        # 1 active turn permitted (heavy_work = 1 control + 1 turn = 2)
        host.handle_frame({"type": "turn.start", "sid": "s1", "request_id": "r1"})
        # 2nd turn must be queued because heavy_work reaches ceiling 2
        host.handle_frame({"type": "turn.start", "sid": "s2", "request_id": "r2"})

        frames = _json_lines(out)
        accepted = [f for f in frames if f["type"] == "turn.accepted"]
        assert len(accepted) == 2
        assert accepted[0]["request_id"] == "r1" and accepted[0]["queue_position"] == 0
        assert accepted[1]["request_id"] == "r2" and accepted[1]["queue_position"] == 1
    finally:
        release_ctrl.set()
        release_turn.set()
        ctrl_thread.join(timeout=2.0)
        server._sessions.pop(sid, None)
        host.close()
