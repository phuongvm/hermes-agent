"""TDD unit & integration tests for Hermes operator session handoff (`POST /api/sessions/{session_id}/handoff`)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms import api_server_session_handoff as handoff_mod
from gateway.platforms.api_server import APIServerAdapter
from gateway.session import SessionStore
from gateway.session_api import bind_api_session
from gateway.session_authority import initialize_session_authority
from gateway.session_contract import AdmissionReceipt, SessionActor, SessionRef, Submission
from gateway.session_local import create_local_session
from hermes_state_mutation_retirement import retire_sessions
from hermes_state_runtime import list_session_admissions

API_KEY = "test-api-server-secret-key-0123456789"
ENABLED_CANONICAL_CFG: dict[str, Any] = {
    "gateway": {"api_server": {"allow_operator_handoff": True}}
}
ENABLED_FALLBACK_CFG: dict[str, Any] = {
    "api_server": {"allow_operator_handoff": True}
}


def _extract_code(body: dict[str, Any]) -> str:
    if isinstance(body.get("code"), str):
        return str(body["code"])
    err = body.get("error")
    if isinstance(err, dict) and isinstance(err.get("code"), str):
        return str(err["code"])
    return ""


def _valid_envelope(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "idempotency_key": "idem-handoff-001",
        "correlation_id": "corr-handoff-001",
        "intent": "REQUEST",
        "hop_count": 1,
        "max_hops": 4,
        "from": {
            "surface": "antigravity",
            "node": "agent4070",
            "session_id": "af6ff0ac-1d63-460a-b633-228ca68bb5a4",
            "author_label": "Phuong / Antigravity (agent4070)",
        },
        "to": {
            "surface": "hermes",
            "node": "asus-vb",
            "profile": "default",
        },
        "safety_mode": "read_only_default",
        "summary": "Verify IRIS migration readiness",
        "body": "Please inspect current IRIS schema state read-only.",
    }
    payload.update(overrides)
    return payload


async def _build_handoff_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config_data: dict[str, Any] | None = None,
    api_key: str = API_KEY,
    multiplex: bool = False,
    auto_schedule: bool = False,
):
    from gateway import run as gw_run

    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        gw_run,
        "_load_gateway_config",
        lambda *_a, **_kw: {"platform_toolsets": {"cli": [], "desktop": []}},
    )
    cfg_dict = config_data if config_data is not None else {}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *_a, **_kw: cfg_dict)

    store = SessionStore(home / "sessions", GatewayConfig(multiplex_profiles=multiplex))
    runner = SimpleNamespace(
        config=GatewayConfig(multiplex_profiles=multiplex),
        session_store=store,
        _session_db=store._db,
        adapters={},
        _draining=False,
        _cached_agent_for=lambda _route: None,
        _adapter_for_source=lambda source: runner.adapters.get(source.platform),
    )
    authority = await initialize_session_authority(
        runner, profile_id=str(home), instance_id="test-handoff-instance"
    )
    if not auto_schedule:
        authority._schedule = lambda _ref: None

    adapter = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0, "key": api_key})
    )
    adapter.gateway_runner = runner
    runner.adapters[Platform.API_SERVER] = adapter

    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    for method, path, handler in adapter._http_route_table():
        app.router.add_route(method, path, handler)
        app.router.add_route(method, f"/p/{{profile}}{path}", handler)

    owner_caps = frozenset(
        {"session:create", "session:read", "session:submit", "session:control"}
    )
    owner_actor = SessionActor("uid:1000", str(home), owner_caps, "desktop-ws")
    desktop_ref = create_local_session(
        authority,
        owner_actor,
        {
            "request_id": "create-desktop-1",
            "source": "gui",
            "cwd": str(home),
            "model": "fixture-model",
            "toolsets": [],
        },
    )
    # Mirror classic/adopted Desktop row `source='desktop'` to assert source preservation
    authority.db._execute_write(
        lambda conn: conn.execute(
            "UPDATE sessions SET source='desktop' WHERE id=?",
            (desktop_ref.session_id,),
        )
    )
    return app, adapter, authority, store, owner_actor, desktop_ref.session_id


def _auth_headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}", **extra}


class TestAuthAndConfigGates:
    @pytest.mark.asyncio
    async def test_rejects_missing_or_invalid_api_key_before_config_or_submit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_called = False

        def _boom_config(*_a: Any, **_kw: Any) -> dict[str, Any]:
            nonlocal config_called
            config_called = True
            raise AssertionError("Config loader must not run before _check_auth succeeds")

        app, adapter, authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path, monkeypatch
        )
        monkeypatch.setattr("hermes_cli.config.load_config", _boom_config)

        check_auth_calls = 0
        orig_check_auth = adapter._check_auth

        def _spy_check_auth(req: web.Request) -> web.Response | None:
            nonlocal check_auth_calls
            check_auth_calls += 1
            return orig_check_auth(req)

        monkeypatch.setattr(adapter, "_check_auth", _spy_check_auth)

        submit_called = False

        async def _boom_submit(*_a: Any, **_kw: Any) -> Any:
            nonlocal submit_called
            submit_called = True
            raise AssertionError("SessionAuthority.submit must not run on 401")

        monkeypatch.setattr(authority, "submit", _boom_submit)

        try:
            async with TestClient(TestServer(app)) as client:
                for bad_headers in ({}, {"Authorization": "Bearer wrong-key-value"}):
                    resp = await client.post(
                        f"/api/sessions/{session_id}/handoff",
                        data="not-even-valid-json{{{",
                        headers=bad_headers,
                    )
                    assert resp.status == 401
                    body = await resp.json()
                    assert _extract_code(body) == "gateway_auth_failed"
                assert check_auth_calls == 2
                assert not config_called
                assert not submit_called
        finally:
            store._db.close()

    @pytest.mark.asyncio
    async def test_unkeyed_adapter_still_fails_closed_with_401(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, _authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path,
            monkeypatch,
            config_data=ENABLED_CANONICAL_CFG,
            api_key="",
        )
        try:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    f"/api/sessions/{session_id}/handoff",
                    json=_valid_envelope(),
                )
                assert resp.status == 401
                body = await resp.json()
                assert _extract_code(body) == "gateway_auth_failed"
        finally:
            store._db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "disabled_cfg",
        [
            {},
            {"gateway": {"api_server": {"allow_operator_handoff": False}}},
            {"api_server": {"allow_operator_handoff": False}},
            {"gateway": {"api_server": {"allow_operator_handoff": "false"}}},
            {
                "gateway": {"api_server": {"allow_operator_handoff": False}},
                "api_server": {"allow_operator_handoff": True},
            },
        ],
    )
    async def test_handoff_fails_closed_when_opt_in_toggle_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        disabled_cfg: dict[str, Any],
    ) -> None:
        app, _adapter, authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=disabled_cfg
        )
        submit_called = False

        async def _boom_submit(*_a: Any, **_kw: Any) -> Any:
            nonlocal submit_called
            submit_called = True
            raise AssertionError("SessionAuthority.submit must not run when handoff_disabled")

        monkeypatch.setattr(authority, "submit", _boom_submit)

        try:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    f"/api/sessions/{session_id}/handoff",
                    json=_valid_envelope(),
                    headers=_auth_headers(),
                )
                assert resp.status == 403
                body = await resp.json()
                assert _extract_code(body) == "handoff_disabled"
                assert not submit_called
        finally:
            store._db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "enabled_cfg",
        [
            ENABLED_CANONICAL_CFG,
            ENABLED_FALLBACK_CFG,
        ],
        ids=["canonical-gateway-api-server", "fallback-flat-api-server"],
    )
    async def test_both_canonical_and_fallback_config_paths_enable_handoff(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        enabled_cfg: dict[str, Any],
    ) -> None:
        app, _adapter, authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=enabled_cfg
        )
        try:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    f"/api/sessions/{session_id}/handoff",
                    json=_valid_envelope(to={"surface": "hermes", "node": "asus-vb", "session_id": session_id}),
                    headers=_auth_headers(),
                )
                assert resp.status == 202
                body = await resp.json()
                assert body["ok"] is True
                assert body["session_id"] == session_id
                assert body["correlation_id"] == "corr-handoff-001"
                assert body["hop_count"] == 1
                assert body["max_hops"] == 4
                assert body["replayed"] is False
                rows = list_session_admissions(authority.db, session_id=session_id, pending_only=False)
                assert len(rows) == 1
                assert rows[0]["admission_id"] == body["admission_id"]
        finally:
            store._db.close()


class TestEnvelopeAndTurnAuthorValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_from",
        [
            None,
            "not-a-dict",
            {},
            {"surface": "antigravity", "node": "", "author_label": ""},
            {"surface": "antigravity", "node": "   ", "author_label": "   "},
            {"surface": "antigravity", "node": "\x00\t", "author_label": "\n\r"},
            {"surface": "antigravity", "node": "agent4070", "author_label": "   "},
            {"surface": "antigravity", "node": "   ", "author_label": "Phuong"},
        ],
    )
    async def test_rejects_blank_or_invalid_sender_with_400_invalid_turn_author(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_from: Any
    ) -> None:
        app, _adapter, authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        submit_called = False

        async def _boom_submit(*_a: Any, **_kw: Any) -> Any:
            nonlocal submit_called
            submit_called = True
            raise AssertionError("submit must not be called on invalid_turn_author")

        monkeypatch.setattr(authority, "submit", _boom_submit)

        try:
            async with TestClient(TestServer(app)) as client:
                env = _valid_envelope()
                if bad_from is None:
                    env.pop("from", None)
                else:
                    env["from"] = bad_from
                resp = await client.post(
                    f"/api/sessions/{session_id}/handoff",
                    json=env,
                    headers=_auth_headers(),
                )
                assert resp.status == 400
                body = await resp.json()
                assert _extract_code(body) == "invalid_turn_author"
                assert not submit_called
        finally:
            store._db.close()

    @pytest.mark.asyncio
    async def test_valid_sender_maps_to_normalized_turn_author_in_committed_admission(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        try:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    f"/api/sessions/{session_id}/handoff",
                    json=_valid_envelope(),
                    headers=_auth_headers(),
                )
                assert resp.status == 202
                rows = list_session_admissions(authority.db, session_id=session_id, pending_only=False)
                assert len(rows) == 1
                committed_author = rows[0]["payload"]["cli_turn_v1"]["turn_author"]
                assert committed_author == {
                    "id": "antigravity:agent4070",
                    "name": "Phuong / Antigravity (agent4070)",
                    "is_bot": True,
                }
        finally:
            store._db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("overrides", "expected_status", "expected_code"),
        [
            ({"schema_version": 2}, 400, "invalid_schema_version"),
            ({"schema_version": True}, 400, "invalid_schema_version"),
            ({"intent": "PING"}, 400, "invalid_intent"),
            ({"intent": "request"}, 400, "invalid_intent"),
            ({"idempotency_key": "   "}, 400, "invalid_idempotency_key"),
            ({"correlation_id": ""}, 400, "invalid_correlation_id"),
            ({"hop_count": 0}, 400, "invalid_hop_count"),
            ({"hop_count": True}, 400, "invalid_hop_count"),
            ({"max_hops": 0}, 400, "invalid_max_hops"),
            ({"summary": "   "}, 400, "invalid_summary"),
            ({"body": "   "}, 400, "invalid_body"),
            ({"body": None}, 400, "invalid_body"),
            ({"to": {"session_id": "different-session"}}, 400, "invalid_target"),
            ({"hop_count": 5, "max_hops": 4}, 429, "max_hops_exceeded"),
            ({"hop_count": 5, "max_hops": 10}, 429, "max_hops_exceeded"),
            ({"hop_count": 3, "max_hops": 2}, 429, "max_hops_exceeded"),
        ],
    )
    async def test_envelope_field_and_hop_ceiling_rejections(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        overrides: dict[str, Any],
        expected_status: int,
        expected_code: str,
    ) -> None:
        app, _adapter, authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        submit_called = False

        async def _boom_submit(*_a: Any, **_kw: Any) -> Any:
            nonlocal submit_called
            submit_called = True
            raise AssertionError("submit must not run on envelope validation failure")

        monkeypatch.setattr(authority, "submit", _boom_submit)

        try:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    f"/api/sessions/{session_id}/handoff",
                    json=_valid_envelope(**overrides),
                    headers=_auth_headers(),
                )
                assert resp.status == expected_status
                body = await resp.json()
                assert _extract_code(body) == expected_code
                assert not submit_called
        finally:
            store._db.close()

    @pytest.mark.asyncio
    async def test_max_hops_is_clamped_to_four_and_header_idempotency_key_supported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, _authority, store, _owner, session_id = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        try:
            async with TestClient(TestServer(app)) as client:
                env = _valid_envelope(hop_count=4, max_hops=99)
                env.pop("idempotency_key")
                resp = await client.post(
                    f"/api/sessions/{session_id}/handoff",
                    json=env,
                    headers=_auth_headers(**{"Idempotency-Key": "hdr-idem-004"}),
                )
                assert resp.status == 202
                body = await resp.json()
                assert body["hop_count"] == 4
                assert body["max_hops"] == 4
                assert body["idempotency_key"] == "hdr-idem-004"
        finally:
            store._db.close()


class TestSessionEligibilityAndLegacyInvariants:
    @pytest.mark.asyncio
    async def test_refuses_missing_retired_archived_cron_bot_room_and_unbound_legacy_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, authority, store, owner_actor, _desktop_sid = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        db = authority.db

        # 1. Archived Desktop session
        archived_ref = create_local_session(
            authority,
            owner_actor,
            {
                "request_id": "create-archived",
                "source": "gui",
                "cwd": str(tmp_path),
                "model": "fixture-model",
                "toolsets": [],
            },
        )
        db.set_session_archived(archived_ref.session_id, True)

        # 2. Deleted / retired session
        retired_sid = "retired-session-001"
        db.create_session(retired_sid, source="api_server")
        db._execute_write(
            lambda conn: (
                retire_sessions(conn, [retired_sid]),
                conn.execute("DELETE FROM sessions WHERE id=?", (retired_sid,)),
            )
        )

        # 3. Cron & bot_room sessions
        db.create_session("cron-sess-001", source="cron")
        db.create_session("bot-room-001", source="bot_room")

        # 4. Unowned legacy desktop & cli rows (no POLICY_PREFIX / LEGACY_PREFIX in state_meta)
        db.create_session("legacy-unowned-desktop", source="desktop")
        db.create_session("legacy-unowned-cli", source="cli")

        try:
            async with TestClient(TestServer(app)) as client:
                # Missing -> 404 not_found
                r_missing = await client.post(
                    "/api/sessions/non-existent-session/handoff",
                    json=_valid_envelope(idempotency_key="idem-miss"),
                    headers=_auth_headers(),
                )
                assert r_missing.status == 404
                assert _extract_code(await r_missing.json()) == "not_found"

                # Retired -> 404 not_found
                r_retired = await client.post(
                    f"/api/sessions/{retired_sid}/handoff",
                    json=_valid_envelope(idempotency_key="idem-ret"),
                    headers=_auth_headers(),
                )
                assert r_retired.status == 404
                assert _extract_code(await r_retired.json()) == "not_found"

                # Archived -> 409 session_not_handoff_eligible
                r_arch = await client.post(
                    f"/api/sessions/{archived_ref.session_id}/handoff",
                    json=_valid_envelope(idempotency_key="idem-arch"),
                    headers=_auth_headers(),
                )
                assert r_arch.status == 409
                assert _extract_code(await r_arch.json()) == "session_not_handoff_eligible"
                assert not list_session_admissions(db, session_id=archived_ref.session_id, pending_only=False)

                # Cron & bot_room -> 409 session_not_handoff_eligible
                for bad_sid, key_suffix in (("cron-sess-001", "cron"), ("bot-room-001", "room")):
                    r_inelig = await client.post(
                        f"/api/sessions/{bad_sid}/handoff",
                        json=_valid_envelope(idempotency_key=f"idem-{key_suffix}"),
                        headers=_auth_headers(),
                    )
                    assert r_inelig.status == 409
                    assert _extract_code(await r_inelig.json()) == "session_not_handoff_eligible"
                    assert not list_session_admissions(db, session_id=bad_sid, pending_only=False)

                # Unowned legacy rows -> 409 legacy_session_unbound without claiming or mutating row
                for legacy_sid in ("legacy-unowned-desktop", "legacy-unowned-cli"):
                    before_row = db.get_session(legacy_sid)
                    r_leg = await client.post(
                        f"/api/sessions/{legacy_sid}/handoff",
                        json=_valid_envelope(idempotency_key=f"idem-{legacy_sid}"),
                        headers=_auth_headers(),
                    )
                    assert r_leg.status == 409
                    assert _extract_code(await r_leg.json()) == "legacy_session_unbound"
                    after_row = db.get_session(legacy_sid)
                    assert after_row is not None
                    assert after_row["session_key"] == before_row["session_key"]
                    assert after_row["chat_id"] == before_row["chat_id"]
                    assert after_row["user_id"] == before_row["user_id"]
                    assert not list_session_admissions(db, session_id=legacy_sid, pending_only=False)
        finally:
            store._db.close()

    @pytest.mark.asyncio
    async def test_admits_eligible_cli_and_api_server_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, authority, store, owner_actor, _desktop_sid = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        cli_ref = create_local_session(
            authority,
            owner_actor,
            {
                "request_id": "create-cli-1",
                "source": "cli",
                "cwd": str(tmp_path),
                "model": "fixture-model",
                "toolsets": [],
            },
        )
        authority.db.create_session("api-bound-1", source="api_server")
        bind_api_session(authority, "api-bound-1")
        authority.db.create_session("api-unbound-created-1", source="api_server")

        try:
            async with TestClient(TestServer(app)) as client:
                for idx, sid in enumerate((cli_ref.session_id, "api-bound-1", "api-unbound-created-1")):
                    resp = await client.post(
                        f"/api/sessions/{sid}/handoff",
                        json=_valid_envelope(idempotency_key=f"idem-elig-{idx}"),
                        headers=_auth_headers(),
                    )
                    assert resp.status == 202
                    body = await resp.json()
                    assert body["ok"] is True
                    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
                    assert len(rows) == 1
                    assert rows[0]["payload"]["local_operator_v1"]["principal_id"] == "api-operator"
        finally:
            store._db.close()


class TestFifoQueueingProvenanceAndAntiLoop:
    @pytest.mark.asyncio
    async def test_desktop_busy_fifo_queueing_preserves_source_user_and_stamps_local_operator_v1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, authority, store, owner_actor, desktop_sid = await _build_handoff_harness(
            tmp_path,
            monkeypatch,
            config_data=ENABLED_CANONICAL_CFG,
            auto_schedule=True,
        )
        turn1_started = asyncio.Event()
        release_turn1 = asyncio.Event()
        executed_turns: list[tuple[str, str, dict[str, Any]]] = []

        async def _fake_execute_finite(auth: Any, ref: SessionRef, row: dict[str, Any]) -> str:
            executed_turns.append((row["principal_id"], row["payload"]["text"], row["payload"]))
            if len(executed_turns) == 1:
                turn1_started.set()
                await release_turn1.wait()
            return f"reply-{len(executed_turns)}"

        monkeypatch.setattr(
            "gateway.session_finite.execute_finite_admission", _fake_execute_finite
        )

        try:
            # 1. Start an active Desktop turn that holds execution
            desktop_ref = SessionRef(authority.profile_id, desktop_sid)
            await authority.submit(
                owner_actor,
                Submission(
                    request_id="desktop-user-turn-1",
                    ref=desktop_ref,
                    payload={"text": "Desktop user turn in flight"},
                    intent="queue",
                ),
            )
            await asyncio.wait_for(turn1_started.wait(), timeout=5.0)

            # 2. While Desktop turn 1 is running (`started`), dispatch operator handoff
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=_valid_envelope(
                        idempotency_key="idem-busy-fifo-1",
                        correlation_id="corr-busy-1",
                        intent="REQUEST",
                        hop_count=1,
                    ),
                    headers=_auth_headers(),
                )
                assert resp.status == 202
                body = await resp.json()
                assert body["ok"] is True
                assert body["status"] == "queued"
                assert body["queue_position"] == 1
                assert body["replayed"] is False

                # Verify queued behind active turn in FIFO
                pending_now = list_session_admissions(authority.db, session_id=desktop_sid, pending_only=False)
                assert [r["status"] for r in pending_now] == ["started", "queued"]
                handoff_row = pending_now[1]
                assert handoff_row["request_id"] == "handoff:idem-busy-fifo-1"
                assert handoff_row["principal_id"] == "api-operator"
                assert handoff_row["payload"]["local_operator_v1"] == {
                    "profile_id": authority.profile_id,
                    "session_id": desktop_sid,
                    "principal_id": "api-operator",
                }
                assert handoff_row["payload"]["cli_turn_v1"]["turn_author"] == {
                    "id": "antigravity:agent4070",
                    "name": "Phuong / Antigravity (agent4070)",
                    "is_bot": True,
                }
                rendered = handoff_row["payload"]["text"]
                assert "Phuong / Antigravity (agent4070)" in rendered
                assert "agent4070" in rendered
                assert "af6ff0ac-1d63-460a-b633-228ca68bb5a4" in rendered
                assert "read_only_default" in rendered
                assert "pause for explicit local user authorization" in rendered
                assert "correlation_id=corr-busy-1" in rendered
                assert "hop_count=2" in rendered

                # Verify original Desktop session `source` and `user_id` remain untouched
                session_row = authority.db.get_session(desktop_sid)
                assert session_row is not None
                assert session_row["source"] == "desktop"
                assert session_row["user_id"] == "uid:1000"

                # 3. Release Desktop turn 1 and confirm FIFO drains turn 1 then handoff turn 2
                release_turn1.set()
                drain_task = authority.sessions[desktop_sid].task
                if drain_task is not None:
                    await asyncio.wait_for(drain_task, timeout=5.0)

                settled_rows = list_session_admissions(authority.db, session_id=desktop_sid, pending_only=False)
                assert [r["status"] for r in settled_rows] == ["terminal", "terminal"]
                assert [t[0] for t in executed_turns] == ["uid:1000", "api-operator"]

                session_after = authority.db.get_session(desktop_sid)
                assert session_after is not None
                assert session_after["source"] == "desktop"
                assert session_after["user_id"] == "uid:1000"
        finally:
            release_turn1.set()
            store._db.close()

    @pytest.mark.asyncio
    async def test_idempotency_key_replay_returns_same_receipt_and_rejects_mutated_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, authority, store, _owner, desktop_sid = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        try:
            async with TestClient(TestServer(app)) as client:
                env = _valid_envelope(idempotency_key="idem-replay-test")
                r1 = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=env,
                    headers=_auth_headers(),
                )
                assert r1.status == 202
                b1 = await r1.json()
                assert b1["replayed"] is False
                assert "Idempotency-Replayed" not in r1.headers

                # Exact retry -> 202 with replayed: True, identical admission_id, no duplicate row
                r2 = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=env,
                    headers=_auth_headers(),
                )
                assert r2.status == 202
                assert r2.headers.get("Idempotency-Replayed") == "true"
                b2 = await r2.json()
                assert b2["replayed"] is True
                assert b2["admission_id"] == b1["admission_id"]

                rows = list_session_admissions(authority.db, session_id=desktop_sid, pending_only=False)
                assert len(rows) == 1

                # Mutated body under same idempotency_key -> 409 admission_conflict
                r_conflict = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=_valid_envelope(
                        idempotency_key="idem-replay-test",
                        body="Different mutated body text!",
                    ),
                    headers=_auth_headers(),
                )
                assert r_conflict.status == 409
                assert _extract_code(await r_conflict.json()) == "admission_conflict"
        finally:
            store._db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_intent", ["REPLY", "FYI"])
    async def test_terminal_reply_or_fyi_durably_closes_correlation_across_authority_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_intent: str
    ) -> None:
        app, adapter, authority, store, _owner, desktop_sid = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        corr_id = f"corr-close-{terminal_intent.lower()}"

        try:
            async with TestClient(TestServer(app)) as client:
                # 1. Initial REQUEST (hop=1) leaves correlation open
                r_req = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=_valid_envelope(
                        intent="REQUEST",
                        hop_count=1,
                        correlation_id=corr_id,
                        idempotency_key=f"idem-open-{terminal_intent}",
                    ),
                    headers=_auth_headers(),
                )
                assert r_req.status == 202
                assert (await r_req.json())["correlation_closed"] is False

                # 2. Terminal REPLY / FYI (hop=2) closes correlation
                term_env = _valid_envelope(
                    intent=terminal_intent,
                    hop_count=2,
                    correlation_id=corr_id,
                    idempotency_key=f"idem-term-{terminal_intent}",
                )
                r_term = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=term_env,
                    headers=_auth_headers(),
                )
                assert r_term.status == 202
                b_term = await r_term.json()
                assert b_term["correlation_closed"] is True
                assert b_term["replayed"] is False

                # 3. Exact retry of the closing REPLY/FYI still replays cleanly with 202 replayed: True
                r_term_retry = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=term_env,
                    headers=_auth_headers(),
                )
                assert r_term_retry.status == 202
                b_retry = await r_term_retry.json()
                assert b_retry["replayed"] is True
                assert b_retry["admission_id"] == b_term["admission_id"]

                # 4. Follow-up with new idempotency_key (both hop=3 and hop=5) -> 409 correlation_closed
                for hop in (3, 5):
                    r_follow = await client.post(
                        f"/api/sessions/{desktop_sid}/handoff",
                        json=_valid_envelope(
                            intent="REQUEST",
                            hop_count=hop,
                            correlation_id=corr_id,
                            idempotency_key=f"idem-follow-{terminal_intent}-{hop}",
                        ),
                        headers=_auth_headers(),
                    )
                    assert r_follow.status == 409
                    assert _extract_code(await r_follow.json()) == "correlation_closed"

                assert len(list_session_admissions(authority.db, session_id=desktop_sid, pending_only=False)) == 2

                # 5. Simulate gateway restart: initialize a fresh SessionAuthority on the same SQLite DB
                runner = adapter.gateway_runner
                runner.adapters = {Platform.API_SERVER: adapter}
                restarted_authority = await initialize_session_authority(
                    runner,
                    profile_id=authority.profile_id,
                    instance_id="restarted-handoff-instance",
                )
                restarted_authority._schedule = lambda _ref: None

                r_after_restart = await client.post(
                    f"/api/sessions/{desktop_sid}/handoff",
                    json=_valid_envelope(
                        intent="REQUEST",
                        hop_count=3,
                        correlation_id=corr_id,
                        idempotency_key=f"idem-after-restart-{terminal_intent}",
                    ),
                    headers=_auth_headers(),
                )
                assert r_after_restart.status == 409
                assert _extract_code(await r_after_restart.json()) == "correlation_closed"
        finally:
            store._db.close()


class TestMultiplexRoutingSecretScrubbingAndSeamInjection:
    @pytest.mark.asyncio
    async def test_profile_multiplexed_route_and_secret_scrubbing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _adapter, authority, store, _owner, desktop_sid = await _build_handoff_harness(
            tmp_path, monkeypatch, config_data=ENABLED_CANONICAL_CFG
        )
        leaked_bridge_tok = "bridge-secret-token-xyz987"
        try:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    f"/p/default/api/sessions/{desktop_sid}/handoff",
                    json=_valid_envelope(
                        idempotency_key="idem-mux-scrub-1",
                        intent="FYI",
                        summary=f"Check auth Bearer {leaked_bridge_tok}",
                        body=(
                            f"Server key={API_KEY} AGY_BRIDGE_TOKEN={leaked_bridge_tok} "
                            "ref=vw://orgs/Inspire-Robotics/password/AGY_BRIDGE_TOKEN"
                        ),
                    ),
                    headers=_auth_headers(),
                )
                assert resp.status == 202
                raw_resp_text = await resp.text()
                assert API_KEY not in raw_resp_text
                assert leaked_bridge_tok not in raw_resp_text

                rows = list_session_admissions(authority.db, session_id=desktop_sid, pending_only=False)
                assert len(rows) == 1
                rendered = rows[0]["payload"]["text"]
                assert API_KEY not in rendered
                assert leaked_bridge_tok not in rendered
                assert "<REDACTED_TOKEN>" in rendered
                assert "vw://orgs/Inspire-Robotics/password/AGY_BRIDGE_TOKEN" in rendered
                assert "STOP" in rendered
                assert "Do NOT send or echo" in rendered
        finally:
            store._db.close()

    @pytest.mark.asyncio
    async def test_direct_seam_injection_with_in_memory_authority_double(self) -> None:
        submitted: list[tuple[SessionActor, Submission]] = []

        class _StubAuthority:
            profile_id = "stub-profile"

            async def submit(self, actor: SessionActor, request: Submission) -> AdmissionReceipt:
                submitted.append((actor, request))
                return AdmissionReceipt(
                    admission_id="adm-stub-1",
                    ref=request.ref,
                    sequence=1,
                    status="queued",
                    outcome=None,
                    authority_epoch=1,
                    execution_generation=None,
                )

        stub_auth = _StubAuthority()
        stub_adapter = SimpleNamespace(_check_auth=lambda _req: None)

        async def _seam_route(req: web.Request) -> web.Response:
            return await handoff_mod.handle_session_handoff(
                stub_adapter,
                req,
                authority=stub_auth,
                config_loader=lambda: ENABLED_CANONICAL_CFG,
            )

        app = web.Application()
        app.router.add_post("/api/sessions/{session_id}/handoff", _seam_route)
        async with TestClient(TestServer(app)) as client:
            env = _valid_envelope(intent="FYI", idempotency_key="stub-idem-1", correlation_id="stub-corr-1")
            r1 = await client.post("/api/sessions/sess-1/handoff", json=env)
            assert r1.status == 202
            assert (await r1.json())["replayed"] is False

            r2 = await client.post("/api/sessions/sess-1/handoff", json=env)
            assert r2.status == 202
            assert (await r2.json())["replayed"] is True
            assert len(submitted) == 1

            r3 = await client.post(
                "/api/sessions/sess-1/handoff",
                json=_valid_envelope(intent="REQUEST", idempotency_key="stub-idem-2", correlation_id="stub-corr-1"),
            )
            assert r3.status == 409
            assert _extract_code(await r3.json()) == "correlation_closed"
