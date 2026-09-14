"""Regression tests for dashboard auth host-ownership and process-home binding.

Verifies active OpenSpec change: stabilize-dashboard-auth-runtime-ownership
(Lane 1: Backend Host-Auth Provider Ownership)
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes_constants import (
    get_hermes_home,
    get_hermes_home_override,
    get_process_hermes_home,
    hermes_home_key,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from hermes_cli.dashboard_auth import (
    DashboardAuthProvider,
    clear_providers,
    get_provider,
    list_providers,
)
from hermes_cli.dashboard_auth.registry import register_global_provider
from hermes_cli.plugins import PluginContext, PluginManifest, PluginManager
from plugins.dashboard_auth.self_hosted import SelfHostedOIDCProvider
from plugins.dashboard_auth.self_hosted.sessions import ACCESS_PREFIX
from tests.plugins.dashboard_auth.test_self_hosted_provider import (
    _CLIENT_ID,
    _ISSUER,
    _make_provider,
    _mint_id_token,
    rsa_keypair,
)


@pytest.fixture(autouse=True)
def _clean_auth_registry():
    clear_providers()
    yield
    clear_providers()


class _DummyAuthProvider(DashboardAuthProvider):
    name: str = "self-hosted"
    display_name: str = "Dummy Provider"

    def __init__(self, name: str = "self-hosted", display_name: str = "Dummy Provider", tag: str = "host"):
        self.name = name
        self.display_name = display_name
        self.tag = tag

    def start_login(self, *, redirect_uri):
        raise NotImplementedError

    def complete_login(self, *, code, state, code_verifier, redirect_uri):
        raise NotImplementedError

    def verify_session(self, *, access_token):
        return None

    def refresh_session(self, *, refresh_token):
        return None

    def revoke_session(self, *, refresh_token):
        return None


def test_self_hosted_provider_binds_to_process_home_by_default(tmp_path, monkeypatch):
    """SelfHostedOIDCProvider binds session store to get_process_hermes_home() by default."""
    root_home = tmp_path / "root_hermes"
    root_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root_home))

    provider = SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=3600)
    assert provider._session_store is not None
    assert provider._session_store.path == root_home / "dashboard-auth-sessions.db"


def test_self_hosted_provider_ignores_hermes_home_override(tmp_path, monkeypatch):
    """Even if an in-flight request sets a HERMES_HOME override, SelfHostedOIDCProvider
    binds to the process home rather than the routed profile directory."""
    root_home = tmp_path / "root_hermes"
    root_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root_home))

    profile_home = tmp_path / "profiles" / "coder"
    profile_home.mkdir(parents=True, exist_ok=True)

    token = set_hermes_home_override(profile_home)
    try:
        assert get_hermes_home() == profile_home
        assert get_process_hermes_home() == root_home

        provider = SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=3600)
        assert provider._session_store is not None
        assert provider._session_store.path == root_home / "dashboard-auth-sessions.db"
        assert provider._session_store.path != profile_home / "dashboard-auth-sessions.db"
    finally:
        reset_hermes_home_override(token)


def test_self_hosted_provider_respects_named_profile_process_home(tmp_path, monkeypatch):
    """When dashboard is explicitly launched with a named profile, it binds to that
    process profile home, NOT forced to a global root."""
    profile_home = tmp_path / "profiles" / "coder"
    profile_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    assert get_process_hermes_home() == profile_home

    provider = SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=3600)
    assert provider._session_store is not None
    assert provider._session_store.path == profile_home / "dashboard-auth-sessions.db"


def test_register_global_provider_rejects_replacement_when_request_scoped(tmp_path, monkeypatch):
    """register_global_provider preserves active host provider when called under a request-scoped override."""
    root_home = tmp_path / "root_hermes"
    root_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root_home))

    host_provider = _DummyAuthProvider(tag="host-active")
    register_global_provider(host_provider)
    assert get_provider("self-hosted") is host_provider

    profile_home = tmp_path / "profiles" / "coder"
    profile_home.mkdir(parents=True, exist_ok=True)

    token = set_hermes_home_override(profile_home)
    try:
        rogue_provider = _DummyAuthProvider(tag="rogue-request-scoped")
        register_global_provider(rogue_provider)

        # Active host provider must NOT be replaced
        active = get_provider("self-hosted")
        assert active is host_provider
        assert getattr(active, "tag", None) == "host-active"
    finally:
        reset_hermes_home_override(token)


def test_plugin_context_register_dashboard_auth_provider_rejects_profile_scope(tmp_path, monkeypatch):
    """PluginContext.register_dashboard_auth_provider rejects overwriting host provider
    when called from a routed profile manager or under home override."""
    root_home = tmp_path / "root_hermes"
    root_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root_home))

    # Host registers its provider
    host_mgr = PluginManager(scope_key=hermes_home_key(root_home))
    host_ctx = PluginContext(PluginManifest(name="self_hosted", version="1.0.0", kind="backend"), host_mgr)
    host_provider = _DummyAuthProvider(tag="host-initial")
    host_ctx.register_dashboard_auth_provider(host_provider)

    assert get_provider("self-hosted") is host_provider

    # A profile manager attempts to register a provider
    profile_home = tmp_path / "profiles" / "coder"
    profile_home.mkdir(parents=True, exist_ok=True)
    profile_mgr = PluginManager(scope_key=hermes_home_key(profile_home))
    profile_ctx = PluginContext(PluginManifest(name="self_hosted", version="1.0.0", kind="backend"), profile_mgr)
    rogue_provider = _DummyAuthProvider(tag="rogue-profile")

    result = profile_ctx.register_dashboard_auth_provider(rogue_provider)
    assert result is None

    # Host provider remains active
    assert get_provider("self-hosted") is host_provider
    assert getattr(get_provider("self-hosted"), "tag", None) == "host-initial"


def test_host_rediscovery_in_host_context_rotates_provider(tmp_path, monkeypatch):
    """Forced rediscovery in the host context legitimately rotates provider in place."""
    root_home = tmp_path / "root_hermes"
    root_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root_home))

    host_mgr = PluginManager(scope_key=hermes_home_key(root_home))
    host_ctx = PluginContext(PluginManifest(name="self_hosted", version="1.0.0", kind="backend"), host_mgr)

    old_provider = _DummyAuthProvider(tag="old")
    new_provider = _DummyAuthProvider(tag="new")

    host_ctx.register_dashboard_auth_provider(old_provider)
    assert get_provider("self-hosted") is old_provider

    host_ctx.register_dashboard_auth_provider(new_provider)
    assert get_provider("self-hosted") is new_provider


def test_root_dashboard_survives_routed_profile_rediscovery(tmp_path, monkeypatch, rsa_keypair):
    """E2E acceptance scenario:
    1. Root dashboard starts under root process home.
    2. User authenticates; session tokens are issued and saved in root session DB.
    3. Routed profile request and plugin rediscovery occurs for profile 'coder'.
    4. Host auth provider remains bound to root DB.
    5. No new session database is created in coder profile directory.
    6. Session refresh using initial refresh token succeeds.
    """
    root_home = tmp_path / "root_hermes"
    root_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(root_home))

    # Seeded provider
    seeded = _make_provider(rsa_keypair)
    host_provider = SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=604800)
    host_provider._discovery = seeded._discovery
    host_provider._discovery_fetched_at = seeded._discovery_fetched_at
    host_provider._jwks_client = seeded._jwks_client

    host_mgr = PluginManager(scope_key=hermes_home_key(root_home))
    host_ctx = PluginContext(PluginManifest(name="self_hosted", version="1.0.0", kind="backend"), host_mgr)
    host_ctx.register_dashboard_auth_provider(host_provider)

    assert get_provider("self-hosted") is host_provider

    # 2. Login
    token = _mint_id_token(rsa_keypair, ttl_seconds=300)
    response = MagicMock(status_code=200)
    response.json.return_value = {"id_token": token, "access_token": "idp-token", "expires_in": 300}
    monkeypatch.setattr("plugins.dashboard_auth.self_hosted.httpx.post", lambda *args, **kwargs: response)

    initial_session = host_provider.complete_login(
        code="code", state="state", code_verifier="verifier", redirect_uri="https://hermes.example/auth/callback"
    )
    assert initial_session.access_token.startswith(ACCESS_PREFIX)
    assert initial_session.refresh_token

    # Verify session in root DB
    assert host_provider.verify_session(access_token=initial_session.access_token) is not None
    root_db = root_home / "dashboard-auth-sessions.db"
    assert root_db.exists()

    # 3. Routed profile request
    coder_home = tmp_path / "profiles" / "coder"
    coder_home.mkdir(parents=True, exist_ok=True)
    coder_db = coder_home / "dashboard-auth-sessions.db"

    home_token = set_hermes_home_override(coder_home)
    try:
        # Simulate plugin rediscovery during routed profile execution
        coder_mgr = PluginManager(scope_key=hermes_home_key(coder_home))
        coder_ctx = PluginContext(PluginManifest(name="self_hosted", version="1.0.0", kind="backend"), coder_mgr)

        coder_provider = SelfHostedOIDCProvider(issuer=_ISSUER, client_id=_CLIENT_ID, session_ttl_seconds=604800)
        coder_ctx.register_dashboard_auth_provider(coder_provider)
    finally:
        reset_hermes_home_override(home_token)

    # 4. Host auth provider must still be the original host provider
    active_provider = get_provider("self-hosted")
    assert active_provider is host_provider

    # 5. No database file in coder directory
    assert not coder_db.exists(), f"Found unexpected DB in routed profile dir: {coder_db}"

    # 6. Session refresh using initial refresh token succeeds
    refreshed_session = active_provider.refresh_session(refresh_token=initial_session.refresh_token)
    assert refreshed_session is not None
    assert refreshed_session.access_token.startswith(ACCESS_PREFIX)
    assert active_provider.verify_session(access_token=refreshed_session.access_token) is not None
