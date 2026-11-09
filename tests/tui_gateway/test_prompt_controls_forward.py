"""Prompt queue controls forward from standalone serve to owning gateway (#140385 / RT-37 followup).

When a session has admissions in the Gateway's durable ledger (queued or unknown), Desktop calls:
- prompt.cancel: to cancel/withdraw a queued prompt or interrupt a started turn
- prompt.resolve_unknown: to discard a turn lost across a gateway restart
- prompt.steer_queued: to deliver queued prompt text as a steer to the running agent
- prompt.replace_queued: to update a queued prompt in place
- prompt.receipt: to query status of an admission

A standalone backend (such as hermes dashboard on port 9119) does not manage the queue FIFO itself;
it forwards these RPC controls to the owner gateway serving the session's profile home.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest

from hermes_state import SessionDB
from hermes_state_runtime import admit_session_input, begin_runtime_epoch
from tui_gateway import server


def _legacy(monkeypatch, db):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    for name in ("_schedule_agent_build", "_schedule_session_cap_enforcement", "_register_session_cwd",
                 "_start_agent_build"):
        monkeypatch.setattr(server, name, lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_restart_completed_failed_agent_build", lambda *_a: False)
    turns, emitted = [], []
    monkeypatch.setattr(server, "_run_after_agent_ready", lambda *a: turns.append(a[1]))
    monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None: emitted.append((event, sid, payload)) or True)
    created = server.handle_request({"id": "c", "method": "session.create", "params": {"source": "desktop"}})
    return created["result"]["session_id"], created["result"]["stored_session_id"]


@pytest.mark.parametrize("method,params_extra,result_extra", [
    ("prompt.cancel", {"admission_id": "adm-1"}, {"status": "terminal", "outcome": "cancelled"}),
    ("prompt.resolve_unknown", {"admission_id": "adm-1", "execution_generation": 1},
     {"status": "terminal", "outcome": "interrupted"}),
    ("prompt.steer_queued", {"admission_id": "adm-1"}, {"status": "started"}),
    ("prompt.replace_queued", {"admission_id": "adm-1", "submission_id": "sub-2", "text": "edited text"},
     {"status": "queued"}),
    ("prompt.receipt", {"admission_id": "adm-1"}, {"status": "queued"}),
])
def test_prompt_queue_controls_forward_to_owner_gateway(monkeypatch, tmp_path, method, params_extra, result_extra):
    """Calling prompt queue controls on standalone serve forwards to the owner gateway."""
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key = _legacy(monkeypatch, db)
    try:
        db.create_session(key, "test")
        epoch = begin_runtime_epoch(db, instance_id="gateway")
        admit_session_input(db, epoch=epoch, principal_id="desktop", session_id=key, request_id="adm-1",
                            payload={"text": "queued question"})

        from tui_gateway import owner_forward
        calls = []

        def fake_discover(home):
            return "test-endpoint"

        def fake_forward_rpc(home, endpoint, m, params, *, resume_session_id=None):
            calls.append((m, dict(params), resume_session_id))
            base_status = {
                "admission_id": params["admission_id"],
                "ref": {"profile_id": "owned", "session_id": key},
                "sequence": 1,
                "status": "queued",
                "outcome": None,
                "authority_epoch": 1,
                "execution_generation": None,
            }
            return {
                **base_status,
                **result_extra,
            }

        monkeypatch.setattr(owner_forward, "discover_owner", fake_discover)
        monkeypatch.setattr(owner_forward, "forward_rpc", fake_forward_rpc)

        req_params = {"session_id": sid, **params_extra}
        reply = server.handle_request({
            "id": f"call-{method}",
            "method": method,
            "params": req_params,
        })

        assert "error" not in reply, reply
        assert reply["result"]["status"] == result_extra["status"]
        assert len(calls) == 1
        assert calls[0][0] == method
        assert calls[0][1]["session_id"] == key
        assert calls[0][1]["admission_id"] == "adm-1"
        assert calls[0][2] == key
    finally:
        server._sessions.pop(sid, None)
        db.close()


def test_prompt_control_refusal_when_gateway_not_running(monkeypatch, tmp_path):
    """When no gateway serves the session's home, prompt control returns 4090 SESSION_NOT_OWNED."""
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key = _legacy(monkeypatch, db)
    try:
        db.create_session(key, "test")
        from tui_gateway import owner_forward
        from tui_gateway.owner_forward import OwnerNotRunning

        def fake_discover(home):
            raise OwnerNotRunning("the gateway that owns it is absent")

        monkeypatch.setattr(owner_forward, "discover_owner", fake_discover)

        reply = server.handle_request({
            "id": "cancel-not-running",
            "method": "prompt.cancel",
            "params": {"session_id": sid, "admission_id": "adm-1"},
        })

        assert reply["error"]["code"] == 4090
        assert reply["error"]["data"] == {"reason": "SESSION_NOT_OWNED"}
    finally:
        server._sessions.pop(sid, None)
        db.close()


def test_prompt_control_owner_refusal_propagates(monkeypatch, tmp_path):
    """When owner gateway returns an RPC error (e.g. not_found, permission_denied), error is returned."""
    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key = _legacy(monkeypatch, db)
    try:
        db.create_session(key, "test")
        from hermes_cli.gateway_client import GatewayRPCError
        from tui_gateway import owner_forward

        def fake_discover(home):
            return "test-endpoint"

        def fake_forward_rpc(home, endpoint, m, params, *, resume_session_id=None):
            raise GatewayRPCError("not_found", code=4040, data={"reason": "not_found"})

        monkeypatch.setattr(owner_forward, "discover_owner", fake_discover)
        monkeypatch.setattr(owner_forward, "forward_rpc", fake_forward_rpc)

        reply = server.handle_request({
            "id": "cancel-refused",
            "method": "prompt.cancel",
            "params": {"session_id": sid, "admission_id": "adm-1"},
        })

        assert reply["error"]["code"] == 4040
        assert reply["error"]["data"] == {"reason": "not_found"}
    finally:
        server._sessions.pop(sid, None)
        db.close()


def test_prompt_cancel_and_resolve_unknown_end_to_end_with_real_session_authority(monkeypatch, tmp_path):
    """End-to-end: standalone serve -> forward_rpc(resume_session_id) -> real SessionAuthority + SessionDB."""
    import contextlib
    from gateway.session_authority import LiveSession, SessionAuthority
    from hermes_state_runtime import claim_session_input, list_session_admissions, recover_session_inputs
    from tests.tui_gateway.test_legacy_submit_forwards_to_owner import _OwnerClient
    from tui_gateway import owner_forward

    db = SessionDB(db_path=tmp_path / "state.db")
    sid, key = _legacy(monkeypatch, db)
    try:
        db.create_session(key, "test")
        epoch1 = begin_runtime_epoch(db, instance_id="gateway-1")
        lost_adm = admit_session_input(db, epoch=epoch1, principal_id="desktop", session_id=key, request_id="req-lost",
                                       payload={"text": "lost turn"})
        claimed = claim_session_input(db, epoch=epoch1, session_id=key)
        queued_adm = admit_session_input(db, epoch=epoch1, principal_id="desktop", session_id=key, request_id="req-q",
                                         payload={"text": "queued turn"})

        # Simulate gateway restart turning the started turn into 'unknown'
        epoch2 = begin_runtime_epoch(db, instance_id="gateway-2")
        recover_session_inputs(db, epoch=epoch2)

        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id="owned", instance_id="gateway-2",
                                     db=db, epoch=epoch2)
        authority.sessions[key] = LiveSession(SimpleNamespace(platform=None, user_id="desktop"), "route")
        monkeypatch.setattr(authority, "_schedule", lambda ref: None)

        @contextlib.asynccontextmanager
        async def connect(_home, endpoint, **_kw):
            assert endpoint == "ready-owner"
            yield _OwnerClient(authority)

        monkeypatch.setattr(owner_forward, "discover_owner", lambda _home: "ready-owner")
        monkeypatch.setattr(owner_forward, "connect_owner", connect)

        # 1. Cancel the queued admission via standalone server.handle_request
        cancel_reply = server.handle_request({
            "id": "e2e-cancel",
            "method": "prompt.cancel",
            "params": {"session_id": sid, "admission_id": queued_adm["admission_id"]},
        })
        assert "error" not in cancel_reply, cancel_reply
        assert cancel_reply["result"]["status"] == "terminal"
        assert cancel_reply["result"]["outcome"] == "cancelled"

        # 2. Resolve the unknown admission via standalone server.handle_request
        resolve_reply = server.handle_request({
            "id": "e2e-resolve",
            "method": "prompt.resolve_unknown",
            "params": {
                "session_id": sid,
                "admission_id": lost_adm["admission_id"],
                "execution_generation": claimed["generation"],
            },
        })
        assert "error" not in resolve_reply, resolve_reply
        assert resolve_reply["result"]["status"] == "terminal"
        assert resolve_reply["result"]["outcome"] == "interrupted"

        # Verify SQLite ledger has no pending admissions left
        assert list_session_admissions(db, session_id=key) == []
    finally:
        server._sessions.pop(sid, None)
        db.close()
