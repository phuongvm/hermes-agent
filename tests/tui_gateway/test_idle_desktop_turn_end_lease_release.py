"""Resume-time Desktop surface preservation and immediate turn-end idle lease release.

Layer 2 of Desktop->Gateway session handoff:
1. ``session.resume`` preserves stored ``source="desktop"`` (and ``track_liveness=True``) when the
   caller omits ``params.source`` and ``HERMES_DESKTOP_TERMINAL`` is unset, without letting gateway
   sources bleed into TUI sessions or overriding explicit caller ``source`` / embedded terminals.
2. Truly-idle Desktop sessions release their ``active_sessions.json`` lease immediately when an
   in-process or isolated compute-host turn settles (after post-turn follow-ups drain), while
   retaining leases across queued follow-ups, interactive waits, local wakeups, delegations,
   off-turn transcript claims, and closing sessions, and preserving 4090 writer fencing +
   out-of-band turn adoption on the next Desktop prompt.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli.active_sessions import active_session_registry_snapshot
from hermes_state import SessionDB
from tui_gateway import server


class _StubResumeAgent:
    model = "test-model"
    provider = "test"

    def close(self) -> None:
        return None


@pytest.fixture
def resume_harness(monkeypatch):
    """Real ``session.resume`` handler + ``SessionDB`` + ``active_sessions.json`` on a daemon without
    ``HERMES_DESKTOP`` set (matching ``hermes dashboard`` on ``:9119``)."""
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    monkeypatch.delenv("HERMES_DESKTOP_TERMINAL", raising=False)
    monkeypatch.delenv("HERMES_TUI_TOOLSETS", raising=False)
    db = SessionDB(db_path=server.get_hermes_home() / "state.db")
    monkeypatch.setattr(server, "_db", db)
    for name, stub in {
        "_resolve_model": lambda: "test-model",
        "_enable_gateway_prompts": lambda: None,
        "_schedule_agent_build": lambda *a, **k: None,
        "_schedule_session_cap_enforcement": lambda *a, **k: None,
        "_maybe_schedule_auto_continue": lambda *a, **k: None,
        "_child_run_active": lambda _key, _home=None: False,
        "_make_agent": lambda *a, **k: _StubResumeAgent(),
        "_wire_session_agent": lambda *a, **k: False,
        "_start_session_services": lambda *a, **k: None,
        "_schedule_mcp_late_refresh": lambda *a, **k: None,
        "_session_info": lambda *a, **k: {},
        "_emit": lambda *a, **k: None,
    }.items():
        monkeypatch.setattr(server, name, stub)
    monkeypatch.setattr(server.git_probe, "branch", lambda _cwd: None)
    monkeypatch.setattr(server.git_probe, "common_repo_root", lambda _cwd: None)
    created_sids: list[str] = []

    def resume(stored_id: str, *, stored_source: str = "desktop", **params) -> tuple[str, dict]:
        db.create_session(stored_id, source=stored_source, model="test-model")
        resp = server.handle_request({
            "id": f"resume-{stored_id}",
            "method": "session.resume",
            "params": {"session_id": stored_id, **params},
        })
        assert "error" not in resp, resp
        sid = resp["result"]["session_id"]
        created_sids.append(sid)
        live = server._sessions[sid]
        if (hydrating := live.get("resume_history_ready")) is not None:
            assert hydrating.wait(5)
        return sid, live

    yield SimpleNamespace(db=db, resume=resume, monkeypatch=monkeypatch)
    for sid in created_sids:
        record = server._sessions.pop(sid, None)
        server._release_active_session_slot(record)
    db.close()


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param({"lazy": True}, id="lazy"),
        pytest.param({"defer_history": True}, id="deferred"),
        pytest.param({}, id="cold"),
        pytest.param({"eager_build": True}, id="eager"),
    ],
)
def test_resume_source_preserves_desktop_and_liveness_across_all_resume_shapes(resume_harness, flags):
    shape_id = next(iter(flags.keys()), "cold")
    stored_id = f"stored-desktop-{shape_id}"
    sid, live = resume_harness.resume(stored_id, stored_source="desktop", **flags)

    assert live["source"] == "desktop"
    assert live.get("active_session_lease") is None
    assert server._ensure_active_session_slot(sid, live) is None
    lease = live["active_session_lease"]
    entries = [e for e in active_session_registry_snapshot() if e["session_id"] == stored_id]
    assert {
        "surface": lease.surface,
        "track_liveness": lease.track_liveness,
        "registry_surface": entries[0]["surface"],
    } == {
        "surface": "desktop",
        "track_liveness": True,
        "registry_surface": "desktop",
    }


def test_resume_source_explicit_param_overrides_stored_desktop(resume_harness):
    sid, live = resume_harness.resume("stored-desktop-explicit-tui", stored_source="desktop", source="tui")

    assert live["source"] == "tui"
    assert server._ensure_active_session_slot(sid, live) is None
    assert live["active_session_lease"].track_liveness is False


def test_resume_source_embedded_terminal_stays_tui(resume_harness):
    resume_harness.monkeypatch.setenv("HERMES_DESKTOP", "1")
    resume_harness.monkeypatch.setenv("HERMES_DESKTOP_TERMINAL", "1")

    sid, live = resume_harness.resume("stored-desktop-embedded-term", stored_source="desktop")

    assert live["source"] == "tui"
    assert "desktop_ui" not in server._gui_surface_toolsets(live["source"])
    assert server._ensure_active_session_slot(sid, live) is None
    assert live["active_session_lease"].track_liveness is False


def test_resume_source_gateway_originated_does_not_bleed_into_tui(resume_harness):
    sid, live = resume_harness.resume("stored-telegram-row", stored_source="telegram")

    assert live["source"] == "tui"
    assert server._ensure_active_session_slot(sid, live) is None
    assert live["active_session_lease"].track_liveness is False


class _LiveTransport:
    _closed = False

    def write(self, _frame):
        return True


def _make_desktop_record(sid: str, **extra) -> dict:
    ready = server.threading.Event()
    ready.set()
    now = server.time.time()
    return {
        "agent": SimpleNamespace(session_id=sid, clear_interrupt=lambda: None),
        "session_key": sid,
        "source": "desktop",
        "history": [],
        "history_lock": server.threading.Lock(),
        "history_version": 0,
        "running": False,
        "agent_ready": ready,
        "transport": _LiveTransport(),
        "created_at": now,
        "last_active": now,
        "attached_images": [],
        "cols": 80,
        **extra,
    }


@pytest.fixture
def eligibility_record(monkeypatch):
    monkeypatch.setattr(server, "_emit", lambda *a, **k: None)
    created: list[tuple[str, dict]] = []

    def build(sid: str = "elig-desktop", *, acquire_lease: bool = True, **extra) -> tuple[str, dict]:
        record = _make_desktop_record(sid, **extra)
        server._sessions[sid] = record
        created.append((sid, record))
        if acquire_lease:
            assert server._ensure_active_session_slot(sid, record) is None
        return sid, record

    yield build
    for sid, record in created:
        server._release_active_session_slot(record)
        server._sessions.pop(sid, None)


def test_eligibility_releases_freshly_settled_desktop_lease_at_zero_min_idle(eligibility_record):
    sid, record = eligibility_record("elig-fresh")
    assert [e["session_id"] for e in active_session_registry_snapshot()] == [sid]

    released = server._release_idle_desktop_lease_if_eligible(sid, record, min_idle_s=0.0)

    assert {
        "released": released,
        "lease": record.get("active_session_lease"),
        "idle_marker": record.get("_lease_idle_released"),
        "still_resident": server._sessions.get(sid) is record,
        "registry": active_session_registry_snapshot(),
    } == {
        "released": True,
        "lease": None,
        "idle_marker": sid,
        "still_resident": True,
        "registry": [],
    }


def _arm_blocker_field(field: str, value):
    def _setup(_sid: str, record: dict, _monkeypatch):
        record[field] = value
        return lambda: None
    return _setup


def _arm_transcript_claim(_sid: str, record: dict, _monkeypatch):
    return server._hold_transcript_claim(record)


def _arm_unready_build(_sid: str, record: dict, _monkeypatch):
    record["agent_ready"] = server.threading.Event()
    return lambda: None


def _arm_non_liveness_lease(sid: str, record: dict, _monkeypatch):
    from hermes_cli.active_sessions import try_acquire_active_session

    server._release_active_session_slot(record)
    lease, refusal = try_acquire_active_session(
        session_id=sid, surface="tui", config={}, metadata={"live_session_id": sid}, track_liveness=False
    )
    assert lease is not None and refusal is None
    record["active_session_lease"] = lease
    return lease.release


def _arm_borrowed_lease(sid: str, record: dict, _monkeypatch):
    from hermes_cli.active_sessions import ActiveSessionLease

    parent_lease = record["active_session_lease"]
    record["active_session_lease"] = ActiveSessionLease(
        lease_id=f"borrowed:{parent_lease.lease_id}", session_id=sid, surface="desktop", enabled=False
    )
    return parent_lease.release


def _arm_pending_kind(sid: str, _record: dict, monkeypatch):
    monkeypatch.setattr(server, "_session_pending_kind", lambda check_sid: "approval" if check_sid == sid else None)
    return lambda: None


def _arm_active_delegation(sid: str, _record: dict, monkeypatch):
    monkeypatch.setattr(server, "_session_has_active_delegations", lambda check_sid, _s: check_sid == sid)
    return lambda: None


def _arm_loop(sid: str, _record: dict, _monkeypatch):
    from hermes_cli.loops import LoopManager

    mgr = LoopManager(sid)
    mgr.set("check status", interval_seconds=1800)
    return mgr.clear


def _arm_heartbeat(sid: str, _record: dict, _monkeypatch):
    from hermes_cli.heartbeat import HeartbeatManager

    mgr = HeartbeatManager(sid)
    mgr.set("heartbeat check", interval_seconds=1800)
    return mgr.clear


def _arm_process_notification(sid: str, _record: dict, _monkeypatch):
    from tools.process_registry import process_registry
    from tools.process_registry_awaiting import release, take_ready

    process_registry.completion_queue.put({"type": "process_exit", "owner_task_id": sid, "session_id": "bg-proc-1"})
    return lambda: release(take_ready(process_registry.completion_queue, 0))


_ELIGIBILITY_BLOCKERS = {
    "queued_prompt": _arm_blocker_field("queued_prompt", {"text": "follow-up"}),
    "queued_prompts": _arm_blocker_field("queued_prompts", [{"text": "second"}]),
    "compute_host_open_request": _arm_blocker_field("_compute_host_open_request", {"request_id": "clarify-1"}),
    "closing": _arm_blocker_field("_closing", True),
    "finalized": _arm_blocker_field("_finalized", True),
    "running": _arm_blocker_field("running", True),
    "compute_host_turn_id": _arm_blocker_field("_compute_host_turn_id", "turn-xyz"),
    "transcript_claim": _arm_transcript_claim,
    "unready_agent_build": _arm_unready_build,
    "non_liveness_tracked": _arm_non_liveness_lease,
    "borrowed_disabled_token": _arm_borrowed_lease,
    "pending_kind": _arm_pending_kind,
    "active_delegation": _arm_active_delegation,
    "local_loop": _arm_loop,
    "local_heartbeat": _arm_heartbeat,
    "pending_process_notification": _arm_process_notification,
}


@pytest.mark.parametrize("blocker_name", sorted(_ELIGIBILITY_BLOCKERS))
def test_eligibility_retains_lease_when_blocker_is_present(eligibility_record, monkeypatch, blocker_name):
    sid, record = eligibility_record(f"elig-{blocker_name}")
    cleanup = _ELIGIBILITY_BLOCKERS[blocker_name](sid, record, monkeypatch)
    held_before = record["active_session_lease"]
    try:
        released = server._release_idle_desktop_lease_if_eligible(sid, record, min_idle_s=0.0)
        assert {
            "released": released,
            "lease_retained": record.get("active_session_lease") is held_before,
            "idle_marker": record.get("_lease_idle_released"),
            "registry_ids": [e["session_id"] for e in active_session_registry_snapshot()],
        } == {
            "released": False,
            "lease_retained": True,
            "idle_marker": None,
            "registry_ids": [sid],
        }
    finally:
        cleanup()


def test_eligibility_rechecks_predicate_under_history_lock_when_followup_queues_in_gap(eligibility_record, monkeypatch):
    sid, record = eligibility_record("elig-lock-race")
    original_pending = server._session_pending_kind
    probed = []

    def queue_in_unlocked_window(check_sid):
        if check_sid == sid and not probed:
            probed.append(check_sid)
            with record["history_lock"]:
                record["queued_prompt"] = {"text": "arrived in gap"}
        return original_pending(check_sid)

    monkeypatch.setattr(server, "_session_pending_kind", queue_in_unlocked_window)

    assert server._release_idle_desktop_lease_if_eligible(sid, record, min_idle_s=0.0) is False
    assert record.get("active_session_lease") is not None
    assert record.get("_lease_idle_released") is None


class _TurnAgent:
    """Deterministic stub agent persisting real ``SessionDB`` rows with ``_row_id`` stamps."""

    def __init__(self, session_id: str, db: SessionDB) -> None:
        self.session_id = session_id
        self._session_db = db
        self.model = "old/model"
        self.provider = "prov"
        self.base_url = ""
        self.api_key = ""
        self.reasoning_config = None
        self.switched: list[str] = []
        self.calls: list[dict] = []
        self._pending_cli_user_message: dict | None = None
        self.on_turn = None

    def clear_interrupt(self) -> None:
        return None

    def switch_model(self, **kw) -> None:
        self.switched.append(kw["new_model"])
        self.model = kw["new_model"]

    def close(self) -> None:
        return None

    def run_conversation(self, prompt, *, conversation_history=None, **_kw) -> dict:
        call_index = len(self.calls)
        hist_copy = [dict(m) for m in (conversation_history or [])]
        self.calls.append({"prompt": prompt, "history": hist_copy})
        extra = self.on_turn(call_index, prompt, hist_copy) if callable(self.on_turn) else {}
        staged = self._pending_cli_user_message
        self._pending_cli_user_message = None
        if isinstance(staged, dict) and staged.get("_row_id") is not None:
            user_msg = dict(staged)
        else:
            uid = self._session_db.append_message(self.session_id, "user", str(prompt))
            user_msg = {"role": "user", "content": str(prompt), "_row_id": uid}
        reply = extra.pop("reply", f"assistant-reply-{call_index}")
        aid = self._session_db.append_message(self.session_id, "assistant", reply)
        assistant_msg = {"role": "assistant", "content": reply, "_row_id": aid}
        return {
            "final_response": reply,
            "messages": [*hist_copy, user_msg, assistant_msg],
            **extra,
        }


@pytest.fixture
def desktop_turn_env(monkeypatch, tmp_path):
    """End-to-end ``tui_gateway`` Desktop session environment with real ``SessionDB`` + ``active_sessions.json``."""
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    monkeypatch.delenv("HERMES_DESKTOP_TERMINAL", raising=False)
    db = SessionDB(db_path=server.get_hermes_home() / "state.db")
    monkeypatch.setattr(server, "_db", db)
    for name, stub in {
        "_emit": lambda *a, **k: None,
        "_wire_callbacks": lambda _sid: None,
        "_sync_agent_model_with_config": lambda _sid, _s: None,
        "_sync_agent_compression_with_config": lambda _sid, _s: None,
        "_sync_agent_fallback_with_config": lambda _sid, _s: None,
        "_sync_bot_capabilities": lambda _sid, _s: None,
        "_session_cwd": lambda _s: str(tmp_path),
        "_register_session_cwd": lambda _s: None,
        "_tts_stream_begin": lambda: None,
        "_get_usage": lambda _a: {},
        "_start_agent_build": lambda _sid, _s: None,
        "_session_uses_compute_host": lambda *a, **k: False,
        "_turn_isolation_enabled": lambda *a, **k: False,
    }.items():
        monkeypatch.setattr(server, name, stub)
    sessions_created: list[tuple[str, dict]] = []
    foreign_leases: list = []

    def create(stored_id: str = "desktop-e2e-chat") -> SimpleNamespace:
        db.create_session(stored_id, source="desktop", model="old/model")
        agent = _TurnAgent(stored_id, db)
        ready = server.threading.Event()
        ready.set()
        now = server.time.time()
        sid = f"ui-{stored_id}"
        record = {
            "agent": agent,
            "agent_error": None,
            "agent_ready": ready,
            "session_key": stored_id,
            "source": "desktop",
            "history": [],
            "history_lock": server.threading.Lock(),
            "history_version": 0,
            "running": False,
            "transport": _LiveTransport(),
            "created_at": now,
            "last_active": now,
            "attached_images": [],
            "image_counter": 0,
            "cols": 80,
            "slash_worker": None,
            "show_reasoning": False,
            "tool_progress_mode": "all",
            "inflight_turn": None,
        }
        server._sessions[sid] = record
        sessions_created.append((sid, record))
        return SimpleNamespace(sid=sid, stored_id=stored_id, record=record, agent=agent, db=db)

    def foreign_claim(stored_id: str, surface: str = "gateway"):
        from hermes_cli.active_sessions import try_acquire_active_session

        lease, refusal = try_acquire_active_session(
            session_id=stored_id,
            surface=surface,
            config={},
            metadata={"live_session_id": f"{surface}-writer"},
        )
        if lease is not None:
            foreign_leases.append(lease)
        return lease, refusal

    yield SimpleNamespace(create=create, foreign_claim=foreign_claim, db=db, monkeypatch=monkeypatch)
    for lease in foreign_leases:
        lease.release()
    for sid, record in sessions_created:
        if (run_thread := record.get("_run_thread")) is not None:
            run_thread.join(timeout=5)
        server._release_active_session_slot(record)
        server._sessions.pop(sid, None)
    db.close()


def _join_session_work(record: dict) -> None:
    from tests.waiting import wait_until

    if (run_thread := record.get("_run_thread")) is not None:
        run_thread.join(timeout=10)
        assert not run_thread.is_alive()
    assert wait_until(lambda: not record.get("running"), timeout=10)


def test_in_process_desktop_turn_fences_mid_turn_then_releases_lease_immediately_at_turn_end(desktop_turn_env):
    from hermes_cli.active_sessions import active_session_liveness_guard

    chat = desktop_turn_env.create("desktop-inproc-turn")
    turn_started = server.threading.Event()
    allow_finish = server.threading.Event()

    def hold_mid_turn(_idx, _prompt, _hist):
        turn_started.set()
        assert allow_finish.wait(10)
        return {}

    chat.agent.on_turn = hold_mid_turn

    resp = server.handle_request({
        "id": "submit-1",
        "method": "prompt.submit",
        "params": {"session_id": chat.sid, "text": "hello from desktop"},
    })
    assert resp["result"]["status"] == "streaming", resp
    assert turn_started.wait(10)

    # Mid-turn: lease is held, Gateway JIT liveness guard is True (HTTP 409 runtime_coordination_required),
    # and a competing surface cannot acquire the session.
    with active_session_liveness_guard(chat.stored_id) as mid_active:
        mid_liveness_guard = mid_active
    mid_lease, mid_refusal = desktop_turn_env.foreign_claim(chat.stored_id, surface="gateway")
    assert {
        "mid_liveness_guard": mid_liveness_guard,
        "mid_foreign_lease": mid_lease,
        "mid_refusal_reason": getattr(mid_refusal, "reason", None),
    } == {
        "mid_liveness_guard": True,
        "mid_foreign_lease": None,
        "mid_refusal_reason": "SESSION_NOT_OWNED",
    }

    allow_finish.set()
    _join_session_work(chat.record)
    from tests.waiting import wait_until

    assert wait_until(lambda: chat.record.get("active_session_lease") is None, timeout=5)

    # Immediately at turn end (0s idle wait, no reaper tick): lease is gone, warm record stays in _sessions,
    # _lease_idle_released is stamped, and Gateway can immediately claim the session.
    with active_session_liveness_guard(chat.stored_id) as post_active:
        post_liveness_guard = post_active
    gw_lease, gw_refusal = desktop_turn_env.foreign_claim(chat.stored_id, surface="gateway")
    assert gw_lease is not None and gw_refusal is None
    assert {
        "post_liveness_guard": post_liveness_guard,
        "post_turn_lease": chat.record.get("active_session_lease"),
        "idle_marker": chat.record.get("_lease_idle_released"),
        "resident_warm": server._sessions.get(chat.sid) is chat.record,
    } == {
        "post_liveness_guard": False,
        "post_turn_lease": None,
        "idle_marker": chat.sid,
        "resident_warm": True,
    }


def test_compute_host_turn_done_releases_parent_lease_while_child_borrowed_token_is_noop(desktop_turn_env):
    parent = desktop_turn_env.create("desktop-compute-host")
    assert server._ensure_active_session_slot(parent.sid, parent.record) is None
    parent_lease = parent.record["active_session_lease"]
    assert parent_lease.track_liveness is True

    # Child side: borrowed lease (enabled=False) running in-process must NOT release the parent's registry entry.
    child_chat = desktop_turn_env.create("desktop-compute-host-child")
    child_chat.record["session_key"] = parent.stored_id
    server._install_borrowed_lease(
        child_chat.sid,
        child_chat.record,
        {
            "source": "desktop",
            "active_session_lease": {"lease_id": parent_lease.lease_id, "session_id": parent.stored_id},
        },
    )
    assert child_chat.record["active_session_lease"].enabled is False
    child_chat.record["running"] = True
    assert server._run_prompt_submit("child-rid", child_chat.sid, child_chat.record, "child turn") is True
    _join_session_work(child_chat.record)
    from tests.waiting import wait_until

    assert wait_until(lambda: not child_chat.record.get("running"), timeout=5)
    assert [e["lease_id"] for e in active_session_registry_snapshot()] == [parent_lease.lease_id]

    # Parent side: _on_compute_host_turn_done with no queued prompt immediately releases the parent's real lease.
    parent.record["running"] = True
    server._on_compute_host_turn_done(
        "parent-rid",
        parent.sid,
        parent.record,
        {"type": "turn.end", "sid": parent.sid, "session_key": parent.stored_id},
    )
    assert {
        "registry": active_session_registry_snapshot(),
        "parent_lease": parent.record.get("active_session_lease"),
        "idle_marker": parent.record.get("_lease_idle_released"),
        "still_resident": server._sessions.get(parent.sid) is parent.record,
    } == {
        "registry": [],
        "parent_lease": None,
        "idle_marker": parent.sid,
        "still_resident": True,
    }


def test_compute_host_turn_done_with_queued_prompt_retains_parent_lease(desktop_turn_env, monkeypatch):
    parent = desktop_turn_env.create("desktop-compute-host-queued")
    assert server._ensure_active_session_slot(parent.sid, parent.record) is None
    parent_lease = parent.record["active_session_lease"]
    dispatched = []

    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *a, **k: True)

    def fake_submit_to_host(rid, sid, session, text, **_kw):
        dispatched.append(text)
        with session["history_lock"]:
            session["_compute_host_turn_id"] = "chained-host-turn-2"
        return {"id": rid, "result": {"status": "streaming", "turn_isolation": True}}

    monkeypatch.setattr(server, "_submit_prompt_to_compute_host", fake_submit_to_host)
    parent.record["running"] = True
    parent.record["queued_prompt"] = {"text": "queued follow-up", "transport": parent.record["transport"]}

    server._on_compute_host_turn_done(
        "parent-rid",
        parent.sid,
        parent.record,
        {"type": "turn.end", "sid": parent.sid, "session_key": parent.stored_id},
    )

    assert {
        "dispatched": dispatched,
        "lease_kept": parent.record.get("active_session_lease") is parent_lease,
        "idle_marker": parent.record.get("_lease_idle_released"),
        "registry_ids": [e["session_id"] for e in active_session_registry_snapshot()],
    } == {
        "dispatched": ["queued follow-up"],
        "lease_kept": True,
        "idle_marker": None,
        "registry_ids": [parent.stored_id],
    }


@pytest.mark.parametrize("followup_kind", ["queued_prompt", "pending_steer", "goal_continuation"])
def test_chained_followups_retain_lease_without_steal_window_then_release_after_chain(
    desktop_turn_env, monkeypatch, followup_kind
):
    from tests.waiting import wait_until

    chat = desktop_turn_env.create(f"desktop-chain-{followup_kind}")
    turn2_started = server.threading.Event()
    allow_turn2_finish = server.threading.Event()

    if followup_kind == "goal_continuation":
        goals = ["continue goal step 2"]
        monkeypatch.setattr(
            server,
            "_goal_followup_after_turn",
            lambda _sid, _s, _res, _st, _raw: goals.pop(0) if goals else None,
        )

    def on_turn(call_idx, _prompt, _hist):
        if call_idx == 0:
            if followup_kind == "queued_prompt":
                with chat.record["history_lock"]:
                    server._enqueue_prompt(chat.record, "user queued turn 2", chat.record["transport"])
                return {}
            if followup_kind == "pending_steer":
                return {"pending_steer": "steered turn 2"}
            return {}
        turn2_started.set()
        assert allow_turn2_finish.wait(10)
        return {}

    chat.agent.on_turn = on_turn

    resp = server.handle_request({
        "id": "chain-1",
        "method": "prompt.submit",
        "params": {"session_id": chat.sid, "text": "turn 1"},
    })
    assert resp["result"]["status"] == "streaming", resp
    assert turn2_started.wait(10)

    # During chained Turn 2: zero steal window — competing surface is still fenced out!
    stolen_lease, refusal = desktop_turn_env.foreign_claim(chat.stored_id, surface="gateway")
    assert stolen_lease is None and getattr(refusal, "reason", None) == "SESSION_NOT_OWNED"

    allow_turn2_finish.set()
    assert wait_until(lambda: chat.record.get("active_session_lease") is None, timeout=10)
    assert len(chat.agent.calls) == 2
    assert chat.record.get("_lease_idle_released") == chat.sid
    assert active_session_registry_snapshot() == []


@pytest.mark.parametrize("refusal_mode", ["stale_generation", "missing_agent", "work_not_started"])
def test_non_closing_early_turn_refusal_releases_desktop_lease_backstop(desktop_turn_env, monkeypatch, refusal_mode):
    chat = desktop_turn_env.create(f"desktop-refusal-{refusal_mode}")
    assert server._ensure_active_session_slot(chat.sid, chat.record) is None
    assert [e["session_id"] for e in active_session_registry_snapshot()] == [chat.stored_id]
    chat.record["running"] = True

    kwargs: dict = {}
    if refusal_mode == "stale_generation":
        chat.record["_queued_prompt_generation"] = 5
        kwargs["queued_prompt_generation"] = 4
    elif refusal_mode == "missing_agent":
        chat.record["agent"] = None
    else:
        monkeypatch.setattr(server, "_start_session_work", lambda *a, **k: None)

    started = server._run_prompt_submit("refusal-rid", chat.sid, chat.record, "hello", **kwargs)

    assert {
        "started": started,
        "lease": chat.record.get("active_session_lease"),
        "idle_marker": chat.record.get("_lease_idle_released"),
        "registry": active_session_registry_snapshot(),
    } == {
        "started": False,
        "lease": None,
        "idle_marker": chat.sid,
        "registry": [],
    }


def test_off_turn_writers_refuse_with_4090_when_competing_surface_claims_turn_end_released_chat(
    desktop_turn_env, monkeypatch
):
    from hermes_cli.model_switch import ModelSwitchResult
    from tests.waiting import wait_until

    chat = desktop_turn_env.create("desktop-fenced-after-turn")
    resp = server.handle_request({
        "id": "turn-1",
        "method": "prompt.submit",
        "params": {"session_id": chat.sid, "text": "desktop question 1"},
    })
    assert resp["result"]["status"] == "streaming", resp
    _join_session_work(chat.record)
    assert wait_until(lambda: chat.record.get("active_session_lease") is None, timeout=5)

    # Competing Gateway handoff claims the chat immediately (no reaper tick!) and appends a turn.
    gw_lease, gw_refusal = desktop_turn_env.foreign_claim(chat.stored_id, surface="gateway")
    assert gw_lease is not None and gw_refusal is None
    chat.db.append_message(chat.stored_id, "user", "gateway user msg")
    chat.db.append_message(chat.stored_id, "assistant", "gateway assistant msg")
    expected_transcript = [
        "desktop question 1",
        "assistant-reply-0",
        "gateway user msg",
        "gateway assistant msg",
    ]

    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        lambda **_kw: ModelSwitchResult(success=True, new_model="other/model", target_provider="prov"),
    )
    monkeypatch.setattr(server, "_expensive_model_confirm", lambda *a, **k: None)
    monkeypatch.setattr(server, "_merge_preflight_warning", lambda *a, **k: None)
    monkeypatch.setattr(
        "agent.conversation_compression_manual.compress_now",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("compress ran on foreign-owned chat")),
    )

    class _SlashWorker:
        def run(self, _cmd):
            raise AssertionError("slash worker ran on foreign-owned chat")

        def pop_seed(self):
            return ""

    monkeypatch.setattr(server, "_slash_worker_for", lambda _rid, _sid, _s: (_SlashWorker(), None))

    class _CheckpointMgr:
        enabled = True

        def unsupported_backend_reason(self, _task_id):
            return None

        def list_checkpoints(self, _cwd):
            return []

        def restore(self, *_a, **_k):
            raise AssertionError("rollback restored foreign-owned chat")

    chat.agent._checkpoint_mgr = _CheckpointMgr()
    pin = {"model": "old/model", "provider": "prov", "base_url": "", "api_key": "", "api_mode": ""}
    chat.record["model_override"] = dict(pin)

    writer_requests = [
        {"method": "config.set", "params": {"session_id": chat.sid, "key": "model", "value": "other/model --provider prov --session"}},
        {"method": "session.compress", "params": {"session_id": chat.sid}},
        {"method": "session.undo", "params": {"session_id": chat.sid}},
        {"method": "rollback.restore", "params": {"session_id": chat.sid, "hash": "abcdef1234567"}},
        {"method": "slash.exec", "params": {"session_id": chat.sid, "command": "/model other/model"}},
        {"method": "slash.exec", "params": {"session_id": chat.sid, "command": "/compress"}},
    ]
    for idx, req in enumerate(writer_requests):
        out = server.handle_request({"id": f"w-{idx}", **req})
        err = out.get("error") or {}
        assert err.get("code") == 4090, (req, out)
        assert (err.get("data") or {}).get("reason") == "SESSION_NOT_OWNED", (req, out)

    actual_transcript = [m["content"] for m in chat.db.get_messages_as_conversation(chat.stored_id)]
    assert actual_transcript == expected_transcript
    assert chat.record["model_override"] == pin
    assert chat.agent.switched == []


def test_next_desktop_prompt_after_gateway_handoff_reacquires_lease_and_adopts_foreign_turns(desktop_turn_env):
    from tests.waiting import wait_until

    chat = desktop_turn_env.create("desktop-roundtrip-handoff")

    # Turn 1 on Desktop -> settles and immediately releases lease.
    resp1 = server.handle_request({
        "id": "rt-1",
        "method": "prompt.submit",
        "params": {"session_id": chat.sid, "text": "desktop turn 1"},
    })
    assert resp1["result"]["status"] == "streaming", resp1
    _join_session_work(chat.record)
    assert wait_until(lambda: chat.record.get("active_session_lease") is None, timeout=5)
    assert chat.record.get("_lease_idle_released") == chat.sid

    # External Gateway handoff acquires lease, appends a handoff exchange to state.db, and releases its lease.
    gw_lease, gw_refusal = desktop_turn_env.foreign_claim(chat.stored_id, surface="gateway")
    assert gw_lease is not None and gw_refusal is None
    chat.db.append_message(chat.stored_id, "user", "gateway handoff prompt")
    chat.db.append_message(chat.stored_id, "assistant", "gateway handoff answer")
    gw_lease.release()

    # Turn 2 on Desktop: lazily re-acquires liveness-tracked lease, clears _lease_idle_released,
    # and folds Gateway's out-of-band messages into model-facing conversation_history.
    observed_turn2: dict = {}

    def inspect_turn2(call_idx, _prompt, hist_copy):
        if call_idx == 1:
            lease = chat.record.get("active_session_lease")
            observed_turn2["has_lease"] = lease is not None
            observed_turn2["track_liveness"] = getattr(lease, "track_liveness", False)
            observed_turn2["idle_marker_present"] = "_lease_idle_released" in chat.record
            observed_turn2["history_contents"] = [m.get("content") for m in hist_copy]
        return {}

    chat.agent.on_turn = inspect_turn2
    resp2 = server.handle_request({
        "id": "rt-2",
        "method": "prompt.submit",
        "params": {"session_id": chat.sid, "text": "desktop turn 2"},
    })
    assert resp2["result"]["status"] == "streaming", resp2
    _join_session_work(chat.record)
    assert wait_until(lambda: chat.record.get("active_session_lease") is None, timeout=5)

    assert observed_turn2 == {
        "has_lease": True,
        "track_liveness": True,
        "idle_marker_present": False,
        "history_contents": [
            "desktop turn 1",
            "assistant-reply-0",
            "gateway handoff prompt",
            "gateway handoff answer",
        ],
    }
    assert chat.record.get("_lease_idle_released") == chat.sid


def test_reaper_backstop_and_concurrency_cap_accounting_after_turn_end_release(desktop_turn_env, monkeypatch):
    from hermes_cli.model_switch import ModelSwitchResult
    from tests.waiting import wait_until

    monkeypatch.setattr(server, "_load_cfg", lambda: {"sessions": {"max_concurrent_sessions": 1}})
    chat_a = desktop_turn_env.create("desktop-cap-a")
    chat_b = desktop_turn_env.create("desktop-cap-b")

    # Chat A runs under max_concurrent_sessions=1, then frees the concurrency slot immediately at turn end
    # while both warm records remain in server._sessions (max_live_sessions resident count unchanged).
    resp_a = server.handle_request({
        "id": "cap-a",
        "method": "prompt.submit",
        "params": {"session_id": chat_a.sid, "text": "hello a"},
    })
    assert resp_a["result"]["status"] == "streaming", resp_a
    _join_session_work(chat_a.record)
    assert wait_until(lambda: chat_a.record.get("active_session_lease") is None, timeout=5)
    assert chat_a.sid in server._sessions and chat_b.sid in server._sessions

    # Chat B can immediately acquire the single concurrency slot without waiting 600s!
    resp_b = server.handle_request({
        "id": "cap-b",
        "method": "prompt.submit",
        "params": {"session_id": chat_b.sid, "text": "hello b"},
    })
    assert resp_b["result"]["status"] == "streaming", resp_b
    _join_session_work(chat_b.record)
    assert wait_until(lambda: chat_b.record.get("active_session_lease") is None, timeout=5)

    # Off-turn writer on Chat A re-claims its lease without running a turn; periodic 600s reaper remains the backstop.
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        lambda **_kw: ModelSwitchResult(success=True, new_model="other/model", target_provider="prov"),
    )
    monkeypatch.setattr(server, "_expensive_model_confirm", lambda *a, **k: None)
    monkeypatch.setattr(server, "_merge_preflight_warning", lambda *a, **k: None)
    sw = server.handle_request({
        "id": "reclaim-a",
        "method": "config.set",
        "params": {"session_id": chat_a.sid, "key": "model", "value": "other/model --provider prov --session"},
    })
    assert "error" not in sw, sw
    assert chat_a.record.get("active_session_lease") is not None
    assert server._release_idle_session_leases(server.time.time() + 60) == 0
    assert server._release_idle_session_leases(server.time.time() + 601) == 1
    assert chat_a.record.get("active_session_lease") is None
    assert chat_a.sid in server._sessions


@pytest.mark.asyncio
async def test_e2e_resumed_desktop_session_fences_gateway_handoff_409_mid_turn_then_accepts_202_at_turn_end(
    tmp_path, monkeypatch
):
    """Full Layer 1 + Layer 2 round-trip: Dashboard resumes a stored ``source="desktop"`` session without
    ``params.source`` (``HERMES_DESKTOP`` unset), fences ``POST /api/sessions/{id}/handoff`` with HTTP 409
    ``runtime_coordination_required`` mid-turn, and immediately allows HTTP 202 once the turn settles."""
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.owner_identity import host_owner_subject
    from tests.gateway.test_api_server_session_handoff import (
        ENABLED_CANONICAL_CFG,
        _auth_headers,
        _build_handoff_harness,
        _extract_code,
        _valid_envelope,
    )
    from tests.waiting import wait_until

    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    monkeypatch.delenv("HERMES_DESKTOP_TERMINAL", raising=False)
    app, _adapter, authority, store, _owner, _seed_sid = await _build_handoff_harness(
        tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
    )
    stored_id = "20261123_152408_b45348"
    authority.db.create_session(
        stored_id,
        source="desktop",
        model="fixture-model",
        cwd=str(tmp_path),
        user_id=host_owner_subject(),
    )

    turn_started = server.threading.Event()
    allow_finish = server.threading.Event()
    turn_agent = _TurnAgent(stored_id, authority.db)

    def hold_turn(_idx, _prompt, _hist):
        turn_started.set()
        assert allow_finish.wait(10)
        return {}

    turn_agent.on_turn = hold_turn
    monkeypatch.setattr(server, "_db", authority.db)
    for name, stub in {
        "_resolve_model": lambda: "fixture-model",
        "_enable_gateway_prompts": lambda: None,
        "_schedule_session_cap_enforcement": lambda *a, **k: None,
        "_maybe_schedule_auto_continue": lambda *a, **k: None,
        "_make_agent": lambda *a, **k: turn_agent,
        "_wire_session_agent": lambda *a, **k: False,
        "_start_session_services": lambda *a, **k: None,
        "_schedule_mcp_late_refresh": lambda *a, **k: None,
        "_session_info": lambda *a, **k: {},
        "_emit": lambda *a, **k: None,
        "_wire_callbacks": lambda _sid: None,
        "_sync_agent_model_with_config": lambda _sid, _s: None,
        "_sync_agent_compression_with_config": lambda _sid, _s: None,
        "_sync_agent_fallback_with_config": lambda _sid, _s: None,
        "_sync_bot_capabilities": lambda _sid, _s: None,
        "_session_cwd": lambda _s: str(tmp_path),
        "_register_session_cwd": lambda _s: None,
        "_tts_stream_begin": lambda: None,
        "_get_usage": lambda _a: {},
        "_start_agent_build": lambda _sid, _s: None,
        "_session_uses_compute_host": lambda *a, **k: False,
        "_turn_isolation_enabled": lambda *a, **k: False,
    }.items():
        monkeypatch.setattr(server, name, stub)
    monkeypatch.setattr(server.git_probe, "branch", lambda _cwd: None)
    monkeypatch.setattr(server.git_probe, "common_repo_root", lambda _cwd: None)

    home_dir = authority.db.db_path.parent
    monkeypatch.setattr(server, "_profile_home", lambda name: home_dir if name == "default" else None)

    ui_sid = None
    try:
        resume_resp = await asyncio.to_thread(
            server.handle_request,
            {
                "id": "res-1",
                "method": "session.resume",
                "params": {"session_id": stored_id, "profile": "default", "eager_build": True},
            },
        )
        assert "error" not in resume_resp, resume_resp
        ui_sid = resume_resp["result"]["session_id"]
        live = server._sessions[ui_sid]
        assert live["source"] == "desktop"

        submit_resp = await asyncio.to_thread(
            server.handle_request,
            {"id": "sub-1", "method": "prompt.submit", "params": {"session_id": ui_sid, "text": "active desktop turn"}},
        )
        assert submit_resp["result"]["status"] == "streaming", submit_resp
        assert await asyncio.to_thread(turn_started.wait, 10)

        async with TestClient(TestServer(app)) as client:
            r_mid = await client.post(
                f"/api/sessions/{stored_id}/handoff",
                json=_valid_envelope(idempotency_key="idem-mid-turn"),
                headers=_auth_headers(),
            )
            mid_body = await r_mid.json()
            assert r_mid.status == 409, mid_body
            assert _extract_code(mid_body) == "runtime_coordination_required"

            allow_finish.set()
            await asyncio.to_thread(_join_session_work, live)
            assert await asyncio.to_thread(
                wait_until, lambda: live.get("active_session_lease") is None, 5
            )

            r_post = await client.post(
                f"/api/sessions/{stored_id}/handoff",
                json=_valid_envelope(idempotency_key="idem-post-turn"),
                headers=_auth_headers(),
            )
            post_body = await r_post.json()
            assert r_post.status == 202, post_body
    finally:
        allow_finish.set()
        if ui_sid is not None:
            record = server._sessions.pop(ui_sid, None)
            if record is not None and (rt := record.get("_run_thread")) is not None:
                await asyncio.to_thread(rt.join, 5)
            server._release_active_session_slot(record)
        store._db.close()
