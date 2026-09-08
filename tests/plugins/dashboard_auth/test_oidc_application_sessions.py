"""OIDC sessions survive ID-token expiry without accepting expired ID tokens."""


import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes_cli.dashboard_auth import InvalidCodeError, ProviderError, RefreshExpiredError, clear_providers, register_provider
from hermes_cli.dashboard_auth import native_flow
from plugins.dashboard_auth.self_hosted import SelfHostedOIDCProvider
from plugins.dashboard_auth.self_hosted import sessions as session_module
from plugins.dashboard_auth.self_hosted.sessions import ACCESS_PREFIX, OIDCSessionStore
from tests.plugins.dashboard_auth.test_self_hosted_provider import _CLIENT_ID, _ISSUER, _make_provider, _mint_id_token, rsa_keypair


@pytest.fixture
def app_provider(tmp_path, monkeypatch, rsa_keypair):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    seeded = _make_provider(rsa_keypair)
    provider = SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=604800)
    provider._discovery = seeded._discovery
    provider._discovery_fetched_at = seeded._discovery_fetched_at
    provider._jwks_client = seeded._jwks_client
    return provider


def login(provider, rsa_keypair, monkeypatch, ttl=300):
    token = _mint_id_token(rsa_keypair, ttl_seconds=ttl)
    response = MagicMock(status_code=200)
    response.json.return_value = {"id_token": token, "access_token": "opaque-idp-token", "expires_in": ttl}
    monkeypatch.setattr("plugins.dashboard_auth.self_hosted.httpx.post", lambda *args, **kwargs: response)
    return provider.complete_login(code="one-time-code", state="state", code_verifier="verifier", redirect_uri="https://hermes.example/auth/callback")


def test_session_and_refresh_survive_multiple_id_token_lifetimes(app_provider, rsa_keypair, monkeypatch):
    initial = login(app_provider, rsa_keypair, monkeypatch)
    assert initial.access_token.startswith(ACCESS_PREFIX)
    assert initial.refresh_token
    now = int(time.time())
    for seconds in [240, 360, 1200, 3600, 86400, 604000]:
        monkeypatch.setattr(session_module.time, "time", lambda: now + seconds)
        current = app_provider.refresh_session(refresh_token=initial.refresh_token)
        verified = app_provider.verify_session(access_token=current.access_token)
        assert verified is not None
        assert verified.user_id == initial.user_id
        assert verified.expires_at <= now + 604800
    monkeypatch.setattr(session_module.time, "time", lambda: now + 604801)
    assert app_provider.verify_session(access_token=current.access_token) is None
    with pytest.raises(RefreshExpiredError):
        app_provider.refresh_session(refresh_token=initial.refresh_token)


def test_restart_keeps_session_and_logout_revokes_all_access_tokens(app_provider, rsa_keypair, monkeypatch):
    initial = login(app_provider, rsa_keypair, monkeypatch)
    restarted = SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=604800)
    assert restarted.verify_session(access_token=initial.access_token).user_id == initial.user_id
    renewed = restarted.refresh_session(refresh_token=initial.refresh_token)
    assert restarted.verify_session(access_token=initial.access_token) is not None
    restarted.revoke_session(refresh_token=initial.refresh_token)
    assert restarted.verify_session(access_token=initial.access_token) is None
    assert restarted.verify_session(access_token=renewed.access_token) is None
    with pytest.raises(RefreshExpiredError):
        restarted.refresh_session(refresh_token=initial.refresh_token)


def test_expired_id_token_cannot_create_application_session(app_provider, rsa_keypair, monkeypatch):
    with pytest.raises(InvalidCodeError):
        login(app_provider, rsa_keypair, monkeypatch, ttl=-60)
    assert not app_provider._session_store.path.exists()


def test_expired_or_forged_raw_jwt_remains_rejected(app_provider, rsa_keypair):
    assert app_provider.verify_session(access_token=_mint_id_token(rsa_keypair, ttl_seconds=-60)) is None
    assert app_provider.verify_session(access_token=ACCESS_PREFIX + "forged") is None


def test_session_store_contains_hashes_not_credentials(app_provider, rsa_keypair, monkeypatch):
    initial = login(app_provider, rsa_keypair, monkeypatch)
    raw = app_provider._session_store.path.read_bytes()
    assert initial.access_token.encode() not in raw
    assert initial.refresh_token.encode() not in raw
    assert b"opaque-idp-token" not in raw


@pytest.mark.parametrize("changed", [{"issuer": "https://other.example"}, {"client_id": "other-client"}, {"ttl_seconds": 3600}])
def test_sessions_are_bound_to_issuer_client_and_ttl(app_provider, rsa_keypair, monkeypatch, changed):
    initial = login(app_provider, rsa_keypair, monkeypatch)
    settings = {"issuer": _ISSUER, "client_id": _CLIENT_ID, "ttl_seconds": 604800, **changed}
    other = OIDCSessionStore(app_provider._session_store.path, **settings)
    assert other.verify(initial.access_token) is None
    with pytest.raises(RefreshExpiredError):
        other.refresh(initial.refresh_token)


def test_access_token_cannot_be_used_to_refresh(app_provider, rsa_keypair, monkeypatch):
    initial = login(app_provider, rsa_keypair, monkeypatch)
    with pytest.raises(RefreshExpiredError):
        app_provider._session_store.refresh(initial.access_token)


def test_concurrent_refresh_keeps_all_in_flight_access_tokens_valid(app_provider, rsa_keypair, monkeypatch):
    initial = login(app_provider, rsa_keypair, monkeypatch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        renewed = list(pool.map(lambda _: app_provider.refresh_session(refresh_token=initial.refresh_token), range(12)))
    assert len({session.access_token for session in renewed}) == 12
    assert all(app_provider.verify_session(access_token=session.access_token) is not None for session in renewed)
    assert app_provider.verify_session(access_token=initial.access_token) is not None


def test_database_outage_is_not_session_expiry(app_provider, rsa_keypair, monkeypatch):
    initial = login(app_provider, rsa_keypair, monkeypatch)
    def broken_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database locked")
    monkeypatch.setattr(session_module, "connect_tracked", broken_connect)
    with pytest.raises(ProviderError, match="store unavailable"):
        app_provider.verify_session(access_token=initial.access_token)
    with pytest.raises(ProviderError, match="store unavailable"):
        app_provider.refresh_session(refresh_token=initial.refresh_token)


@pytest.mark.parametrize("ttl", [-1, True, "604800", 1.5])
def test_invalid_ttl_is_not_silently_ignored(ttl):
    with pytest.raises(ValueError, match="session_ttl_seconds"):
        SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=ttl)


def test_configured_ttl_is_wired_to_provider(monkeypatch, tmp_path):
    import plugins.dashboard_auth.self_hosted as plugin
    monkeypatch.setattr(plugin, "_load_config_oauth_section", lambda: {"issuer": _ISSUER, "client_id": _CLIENT_ID, "session_ttl_seconds": 604800})
    for name in ["HERMES_DASHBOARD_OIDC_ISSUER", "HERMES_DASHBOARD_OIDC_CLIENT_ID"]:
        monkeypatch.delenv(name, raising=False)
    assert plugin._settings()["session_ttl_seconds"] == 604800


def test_native_http_refresh_ws_ticket_and_logout_after_id_token_expiry(app_provider, rsa_keypair, monkeypatch):
    from hermes_cli import web_server
    initial = login(app_provider, rsa_keypair, monkeypatch)
    clear_providers()
    register_provider(app_provider)
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_host", "hermes.example", raising=False)
    client = TestClient(web_server.app, base_url="https://hermes.example")
    now = int(time.time())
    try:
        for seconds in [241, 361, 1201, 3601]:
            monkeypatch.setattr(session_module.time, "time", lambda: now + seconds)
            response = client.post("/auth/native/refresh", json={"refresh_token": initial.refresh_token, "provider": "self-hosted"})
            assert response.status_code == 200
            assert not response.cookies
            token = response.json()["access_token"]
            headers = {"Authorization": "Bearer " + token}
            assert client.get("/api/auth/me", headers=headers).status_code == 200
            assert client.post("/api/auth/ws-ticket", headers=headers).status_code == 200
        response = client.post("/auth/native/logout", json={"refresh_token": initial.refresh_token, "provider": "self-hosted"})
        assert response.status_code == 200
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert client.post("/auth/native/refresh", json={"refresh_token": initial.refresh_token, "provider": "self-hosted"}).status_code == 401
    finally:
        clear_providers()
        native_flow._reset_for_tests()


def test_browser_cookie_session_refreshes_and_revokes_after_original_jwt_expiry(app_provider, rsa_keypair, monkeypatch):
    from hermes_cli import web_server
    from hermes_cli.dashboard_auth.cookies import SESSION_AT_COOKIE, SESSION_RT_COOKIE, SESSION_PROVIDER_COOKIE
    initial = login(app_provider, rsa_keypair, monkeypatch)
    clear_providers()
    register_provider(app_provider)
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_host", "hermes.example", raising=False)
    client = TestClient(web_server.app, base_url="https://hermes.example")
    client.cookies.set("__Host-" + SESSION_AT_COOKIE, initial.access_token)
    client.cookies.set("__Host-" + SESSION_RT_COOKIE, initial.refresh_token)
    client.cookies.set("__Host-" + SESSION_PROVIDER_COOKIE, "self-hosted")
    now = int(time.time())
    try:
        monkeypatch.setattr(session_module.time, "time", lambda: now + 1200)
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["user_id"] == initial.user_id
        assert "hermes-oidc-at." in response.headers["set-cookie"]
        response = client.post("/auth/logout", follow_redirects=False)
        assert response.status_code == 302
        with pytest.raises(RefreshExpiredError):
            app_provider.refresh_session(refresh_token=initial.refresh_token)
    finally:
        clear_providers()


def test_real_provider_native_pkce_login_issues_renewable_application_session(app_provider, rsa_keypair, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from hermes_cli import web_server
    from tests.hermes_cli.test_dashboard_auth_native_flow import _make_pkce

    token = _mint_id_token(rsa_keypair, ttl_seconds=300)
    response = MagicMock(status_code=200)
    response.json.return_value = {"id_token": token, "access_token": "opaque", "expires_in": 300}
    monkeypatch.setattr("plugins.dashboard_auth.self_hosted.httpx.post", lambda *args, **kwargs: response)
    clear_providers()
    native_flow._reset_for_tests()
    register_provider(app_provider)
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_host", "hermes.example", raising=False)
    client = TestClient(web_server.app, base_url="https://hermes.example", follow_redirects=False)
    verifier, challenge = _make_pkce()
    try:
        authorize = client.get("/auth/native/authorize", params={"provider": "self-hosted", "code_challenge": challenge, "code_challenge_method": "S256", "redirect_uri": "http://127.0.0.1:53999/cb", "state": "desktop-state"})
        assert authorize.status_code == 302
        state = parse_qs(urlparse(authorize.headers["location"]).query)["state"][0]
        callback = client.get("/auth/callback", params={"state": state, "code": "idp-code"})
        assert callback.status_code == 302
        code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]
        exchanged = client.post("/auth/native/token", json={"code": code, "code_verifier": verifier})
        assert exchanged.status_code == 200
        assert not exchanged.cookies
        native = exchanged.json()
        assert native["access_token"].startswith(ACCESS_PREFIX)
        assert native["refresh_token"]
        now = int(time.time())
        monkeypatch.setattr(session_module.time, "time", lambda: now + 1200)
        renewed = client.post("/auth/native/refresh", json={"refresh_token": native["refresh_token"], "provider": "self-hosted"})
        assert renewed.status_code == 200
        headers = {"Authorization": "Bearer " + renewed.json()["access_token"]}
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        ticket = client.post("/api/auth/ws-ticket", headers=headers)
        assert ticket.status_code == 200
        from hermes_cli.dashboard_auth.ws_tickets import consume_ticket
        assert consume_ticket(ticket.json()["ticket"])["user_id"] == native["user_id"]
    finally:
        clear_providers()
        native_flow._reset_for_tests()
