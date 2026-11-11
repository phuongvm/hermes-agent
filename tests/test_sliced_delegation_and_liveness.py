"""Slice 4 / Module D3 — ``SlicedDelegationProtocol`` red-loop tests.

Seams under test (public, behaviour-level):
  * ``tools.sliced_delegation`` — the deep module: timeout bound, manifest checkpoint (``os.fsync``),
    heartbeat cadence, silent-stream cutover rule, probe offloading pool.
  * ``tools.delegate_tool`` — ``MAX_SLICE_TIMEOUT_S`` preflight rejection of monolithic ``timeout_s``.
  * ``tools.delegate_tool_child_run`` — ``slice{N}_manifest.json`` written + fsynced before return.
  * ``agent.turn_liveness`` — ``subagent.heartbeat`` every ``60s``.
  * ``agent.chat_completion_stream_monitor`` — ``180s`` silent SSE cutover constant.
  * ``hermes_cli.web_server`` — ``asyncio.to_thread`` offload pool ``max_workers=4``, stall p99 ``< 1.0s``.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import sliced_delegation as sd


# --------------------------------------------------------------------------------------------- constants
def test_protocol_constants_match_design():
    assert sd.MAX_SLICE_TIMEOUT_S == 1800
    assert sd.HEARTBEAT_INTERVAL_S == 60.0
    assert sd.SILENT_STREAM_CUTOVER_S == 180.0
    assert sd.PROBE_POOL_MAX_WORKERS == 4
    assert sd.HEARTBEAT_EVENT == "subagent.heartbeat"


# --------------------------------------------------------------------------------------------- timeout bound
def test_validate_slice_timeout_accepts_bounded_and_rejects_monolithic():
    assert sd.validate_slice_timeout(None) is None
    assert sd.validate_slice_timeout(1800) is None
    assert sd.validate_slice_timeout(900.0) is None
    err = sd.validate_slice_timeout(1801)
    assert err is not None and "1800" in err and "slice" in err.lower()
    assert sd.validate_slice_timeout("abc") is not None


def test_delegate_task_rejects_timeout_above_1800_at_preflight():
    from tools import delegate_tool
    assert delegate_tool.MAX_SLICE_TIMEOUT_S == 1800
    parent = mock.MagicMock()
    parent._delegate_depth = 0
    with mock.patch.object(delegate_tool, "is_spawn_paused", return_value=False):
        out = delegate_tool.delegate_task(goal="implement slice 4 end to end", parent_agent=parent, timeout_s=3600)
    payload = json.loads(out)
    assert "error" in payload
    assert "1800" in payload["error"]


def test_delegate_task_task_level_timeout_above_1800_rejected():
    from tools import delegate_tool
    parent = mock.MagicMock()
    parent._delegate_depth = 0
    tasks = [{"goal": "a sufficiently long and self-contained goal for the batch gate", "timeout_s": 7200}]
    with mock.patch.object(delegate_tool, "is_spawn_paused", return_value=False):
        out = delegate_tool.delegate_task(tasks=tasks, parent_agent=parent)
    payload = json.loads(out)
    assert "error" in payload and "1800" in payload["error"]


# --------------------------------------------------------------------------------------------- manifest checkpoint
def test_write_slice_manifest_fsyncs_and_round_trips(tmp_path: Path):
    calls: list[int] = []
    real_fsync = os.fsync

    def spy(fd):
        calls.append(fd)
        return real_fsync(fd)

    manifest = sd.SliceManifest(
        slice_id="slice4", repo_root=str(tmp_path), head_sha="0" * 40,
        modified_files_sha256={"a.py": "ab" * 32}, proof_commands=("pytest -q",), proof_exit_code=0,
    )
    with mock.patch("tools.sliced_delegation.os.fsync", side_effect=spy):
        path = sd.write_slice_manifest(tmp_path, manifest)
    assert path.name == "slice4_manifest.json"
    assert calls, "os.fsync must be called before the manifest write returns"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["slice_id"] == "slice4"
    assert data["proof_exit_code"] == 0
    assert data["timestamp_utc"].endswith("Z")
    assert set(data) >= {"slice_id", "repo_root", "head_sha", "modified_files_sha256", "proof_commands",
                         "proof_exit_code", "timestamp_utc"}


def test_slice_manifest_rejects_non_canonical_slice_id(tmp_path: Path):
    with pytest.raises(ValueError):
        sd.SliceManifest(slice_id="slice9", repo_root=str(tmp_path), head_sha="", modified_files_sha256={},
                         proof_commands=(), proof_exit_code=0)


def test_child_run_checkpoint_writes_manifest_before_return(tmp_path: Path):
    from tools import delegate_tool_child_run as cr
    child = mock.MagicMock()
    child._delegate_slice_id = "slice2"
    child._delegate_manifest_dir = str(tmp_path)
    child._delegate_output_schema = None
    run = cr._ChildRun(child, mock.MagicMock(), 0, "goal", None, None)
    run.child_task_id = "child-x"
    entry = {"status": "completed", "summary": "done", "api_calls": 3, "duration_seconds": 1.0}
    result = {"messages": []}
    fsynced = []
    real = os.fsync
    with mock.patch("tools.sliced_delegation.os.fsync", side_effect=lambda fd: (fsynced.append(fd), real(fd))[1]):
        out = run.checkpoint_slice_manifest(result, entry)
    assert fsynced
    path = tmp_path / "slice2_manifest.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["slice_id"] == "slice2" and data["proof_exit_code"] == 0
    assert out["slice_manifest"] == str(path)


def test_child_run_checkpoint_noop_without_slice_id(tmp_path: Path):
    from tools import delegate_tool_child_run as cr
    child = mock.MagicMock(spec=[])  # no _delegate_slice_id attribute
    run = cr._ChildRun(child, mock.MagicMock(), 0, "goal", None, None)
    entry = {"status": "completed"}
    assert run.checkpoint_slice_manifest({}, entry) is entry
    assert "slice_manifest" not in entry


# --------------------------------------------------------------------------------------------- heartbeat
def test_subagent_heartbeat_emits_every_60s_of_elapsed_time():
    events: list[tuple[str, dict]] = []
    hb = sd.SubagentHeartbeat(lambda kind, **kw: events.append((kind, kw)), subagent_id="sa-1", interval_s=60.0)
    assert hb.tick(now=0.0) is False
    assert hb.tick(now=59.9) is False
    assert hb.tick(now=60.0) is True
    assert hb.tick(now=90.0) is False
    assert hb.tick(now=120.0) is True
    kinds = [k for k, _ in events]
    assert kinds == ["subagent.heartbeat", "subagent.heartbeat"]
    assert events[0][1]["subagent_id"] == "sa-1"
    assert events[0][1]["elapsed_s"] == pytest.approx(60.0)


def test_turn_liveness_exposes_subagent_heartbeat_cadence():
    from agent import turn_liveness
    assert turn_liveness.SUBAGENT_HEARTBEAT_INTERVAL_S == 60.0
    events: list[str] = []
    hb = turn_liveness.subagent_heartbeat(lambda kind, **kw: events.append(kind), subagent_id="x")
    hb.tick(now=0.0)
    hb.tick(now=61.0)
    assert events == ["subagent.heartbeat"]


# --------------------------------------------------------------------------------------------- silent stream cutover
def test_silent_stream_cutover_rule_fires_only_after_180s_of_zero_chunks():
    assert sd.should_cut_over_silent_stream(last_chunk_at=0.0, now=179.9, chunks_delivered=0) is False
    assert sd.should_cut_over_silent_stream(last_chunk_at=0.0, now=180.1, chunks_delivered=0) is True
    # A stream that delivered chunks is governed by the per-provider stale timer, not the silent cutover.
    assert sd.should_cut_over_silent_stream(last_chunk_at=0.0, now=500.0, chunks_delivered=3) is False


def test_stream_monitor_silent_cutover_constant_and_default_patience():
    from agent import chat_completion_stream_monitor as mon
    from agent import chat_completion_helpers as helpers
    assert mon.SILENT_STREAM_CUTOVER_S == 180.0
    base, explicit = helpers._stream_env_stale_base()
    if not explicit:
        assert base == 180.0


# --------------------------------------------------------------------------------------------- probe offload
def test_probe_pool_is_bounded_to_four_workers():
    pool = sd.probe_pool()
    assert pool._max_workers == sd.PROBE_POOL_MAX_WORKERS == 4
    assert sd.probe_pool() is pool  # process-wide singleton


def test_offload_probe_keeps_event_loop_stall_p99_below_one_second():
    """20 blocking 150ms probes offloaded via ``asyncio.to_thread`` on the 4-worker pool: the loop keeps
    ticking at 10ms, and the p99 tick drift stays < 1.0s (a synchronous probe would stall the loop 3s)."""

    def blocking_probe(i: int) -> int:
        time.sleep(0.15)
        return i

    async def scenario() -> tuple[list[int], list[float]]:
        drifts: list[float] = []
        stop = asyncio.Event()

        async def ticker():
            loop = asyncio.get_running_loop()
            expected = loop.time() + 0.01
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = loop.time()
                drifts.append(max(0.0, now - expected))
                expected = now + 0.01

        t = asyncio.create_task(ticker())
        results = await asyncio.gather(*(sd.offload_probe(blocking_probe, i) for i in range(20)))
        stop.set()
        await t
        return list(results), drifts

    results, drifts = asyncio.run(scenario())
    assert sorted(results) == list(range(20))
    assert drifts
    p99 = statistics.quantiles(drifts, n=100)[98] if len(drifts) >= 100 else max(drifts)
    assert p99 < 1.0, p99


def test_web_server_exports_probe_offload_seam():
    from hermes_cli import web_server
    assert web_server.PROBE_POOL_MAX_WORKERS == 4
    assert callable(web_server.offload_probe)
    assert callable(web_server.offload_eager_reconcile)
    with mock.patch("hermes_cli.web_server._eager_reconcile_own_session_db") as mock_rec:
        asyncio.run(web_server.offload_eager_reconcile())
        mock_rec.assert_called_once()


def test_checkpoint_slice_manifest_fails_closed_on_error(tmp_path: Path):
    from tools import delegate_tool_child_run as cr
    child = mock.MagicMock()
    child._delegate_slice_id = "slice3"
    child._delegate_manifest_dir = str(tmp_path)
    run = cr._ChildRun(child, mock.MagicMock(), 0, "goal", None, None)
    entry = {"status": "completed"}
    with mock.patch("tools.sliced_delegation.write_slice_manifest", side_effect=OSError("disk full")):
        with pytest.raises(RuntimeError) as exc_info:
            run.checkpoint_slice_manifest({}, entry)
        assert "Mandatory slice manifest checkpoint failed" in str(exc_info.value)
        assert entry["status"] == "error"


def test_child_run_times_out_on_slice_deadline(tmp_path: Path):
    from tools import delegate_tool_child_run as cr
    child = mock.MagicMock()
    child._delegate_slice_id = "slice1"
    child._delegate_timeout_s = 0.05
    child.run_conversation = lambda **kw: time.sleep(0.3)
    run = cr._ChildRun(child, mock.MagicMock(), 0, "goal", "sa-test", None)
    res, err_entry, deferred = run.await_child()
    assert res is None
    assert err_entry is not None
    assert err_entry["status"] == "timeout"
    assert "slice timeout" in err_entry["error"]


def test_delegate_task_forwards_slice_id_and_timeout():
    from tools import delegate_tool
    parent = mock.MagicMock()
    parent._delegate_depth = 0
    creds = {"model": "m", "provider": "ai-router", "base_url": "http://x", "api_key": "k", "api_mode": "chat_completions"}
    with mock.patch.object(delegate_tool, "is_spawn_paused", return_value=False), \
         mock.patch("tools.delegate_tool._routes_for", return_value=([mock.MagicMock(error=None, creds=creds, routing_cfg={})], None)), \
         mock.patch("tools.delegate_tool._build_child_preserving_parent_tools") as mock_build, \
         mock.patch("tools.delegate_tool._run_batch", return_value="{}"):
        built_child = mock.MagicMock()
        mock_build.return_value = built_child
        delegate_tool.delegate_task(
            tasks=[{"goal": "a self-contained goal for the task", "slice_id": "slice4", "manifest_dir": "/tmp/manifests", "timeout_s": 900}],
            parent_agent=parent,
        )
        assert getattr(built_child, "_delegate_slice_id", None) == "slice4"
        assert getattr(built_child, "_delegate_manifest_dir", None) == "/tmp/manifests"
        assert getattr(built_child, "_delegate_timeout_s", None) == 900.0


def test_turn_liveness_watchdog_ticks_subagent_heartbeat():
    from agent.turn_liveness import TurnLivenessWatchdog
    agent = mock.MagicMock()
    events = []
    agent.tool_progress_callback = lambda kind, **kw: events.append((kind, kw))
    agent._subagent_id = "sa-child-1"
    agent._last_activity_ts = 100.0
    agent._turn_liveness_activity_generation = 1
    stop_event = mock.MagicMock()
    stop_event.is_set.return_value = False
    lock = mock.MagicMock()
    watchdog = TurnLivenessWatchdog(
        agent, session_id="s1", timeout_s=600.0, poll_s=15.0,
        stop_event=stop_event, activity_lock=lock,
        is_turn_active=lambda: True, commit_abort=lambda s, m: True,
        deactivate_turn=lambda: None,
    )
    assert watchdog._subagent_hb is not None
    with mock.patch("tools.sliced_delegation.time.monotonic", side_effect=[0.0, 61.0, 61.0, 122.0]):
        watchdog._tick()
        watchdog._tick()
    assert any(k == "subagent.heartbeat" for k, _ in events)


def test_stream_monitor_cuts_over_silent_stream_at_180s():
    from agent.chat_completion_stream_monitor import StreamingWaitMonitor
    import threading
    mon = StreamingWaitMonitor()
    agent = mock.MagicMock()
    agent.base_url = ""
    agent._interrupt_requested = False
    setattr(mon, "agent", agent)
    setattr(mon, "api_kwargs", {"model": "cb-agent-coder"})
    setattr(mon, "_stream_stale_timeout", 900.0)
    setattr(mon, "_call_done", threading.Event())
    setattr(mon, "last_chunk_time", {"t": 0.0})
    clients = mock.MagicMock()
    clients.diag = {"chunks": 0}
    setattr(mon, "clients", clients)
    setattr(mon, "stream_attempt_lock", threading.Lock())
    setattr(mon, "stream_attempt_state", {"current": 1, "cancelled": set()})
    setattr(mon, "_abort_for_interrupt", mock.MagicMock())
    killed = []
    setattr(mon, "_kill_stale_stream", lambda elapsed: (killed.append(elapsed), getattr(mon, "_call_done").set()))
    with mock.patch("agent.chat_completion_stream_monitor.time.time", side_effect=[0.0, 0.0, 181.0, 181.0, 181.0]):
        mon._poll_until_done()
    assert len(killed) == 1
    assert killed[0] >= 180.0
