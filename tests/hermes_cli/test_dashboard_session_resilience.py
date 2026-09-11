"""Tests for dashboard session token persistence and startup grace period.

Addresses OpenSpec change desktop-reconnect-session-resilience:
Milestone 1: Persistent Session Token Resolution
Milestone 2: Startup Grace Period (503 Response)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hermes_constants import get_hermes_home
from hermes_cli import web_server
from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_cli.dashboard_auth import clear_providers, register_provider
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider


# ---------------------------------------------------------------------------
# Milestone 1: Persistent Session Token Resolution
# ---------------------------------------------------------------------------

class TestDashboardSessionTokenPersistence:
    """Tests for durable on-disk session token fallback."""

    def test_first_boot_creates_token_file(self, monkeypatch):
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        token_file = get_hermes_home() / ".dashboard_session_token"
        if token_file.exists():
            token_file.unlink()

        token = web_server._resolve_session_token()
        assert token
        assert isinstance(token, str)
        assert len(token) >= 32
        assert token_file.is_file()
        assert token_file.read_text(encoding="utf-8").strip() == token

    def test_subsequent_boot_reads_existing_token_file(self, monkeypatch):
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        token_file = get_hermes_home() / ".dashboard_session_token"
        expected_token = "persisted-secret-token-abcdef123456"
        token_file.write_text(expected_token + "\n", encoding="utf-8")

        token = web_server._resolve_session_token()
        assert token == expected_token

    def test_env_var_takes_precedence_over_file(self, monkeypatch):
        monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "env-token-override")
        token_file = get_hermes_home() / ".dashboard_session_token"
        token_file.write_text("file-token-should-be-ignored\n", encoding="utf-8")

        token = web_server._resolve_session_token()
        assert token == "env-token-override"
        # Verify file content was not overwritten
        assert token_file.read_text(encoding="utf-8").strip() == "file-token-should-be-ignored"

    def test_empty_file_regenerates_token(self, monkeypatch):
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        token_file = get_hermes_home() / ".dashboard_session_token"
        token_file.write_text("   \n", encoding="utf-8")

        token = web_server._resolve_session_token()
        assert token
        assert token.strip() != ""
        assert token_file.read_text(encoding="utf-8").strip() == token

    def test_corrupted_file_regenerates_token(self, monkeypatch):
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        token_file = get_hermes_home() / ".dashboard_session_token"
        # Write invalid binary data
        token_file.write_bytes(b"\xff\xfe\x00\x00\x80\x90\xaa")

        token = web_server._resolve_session_token()
        assert token
        assert isinstance(token, str)
        assert len(token) >= 32
        assert token_file.read_text(encoding="utf-8").strip() == token


# ---------------------------------------------------------------------------
# Milestone 2: Startup Grace Period (503 Response)
# ---------------------------------------------------------------------------

@pytest.fixture
def gated_client():
    """Configure web_server.app for gated mode with StubAuthProvider."""
    clear_providers()
    register_provider(StubAuthProvider())
    prev_host = getattr(web_server.app.state, "bound_host", None)
    prev_port = getattr(web_server.app.state, "bound_port", None)
    prev_required = getattr(web_server.app.state, "auth_required", None)
    prev_startup_ready = getattr(web_server, "_startup_ready", True)

    web_server.app.state.bound_host = "fly-app.fly.dev"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True

    client = TestClient(web_server.app, base_url="https://fly-app.fly.dev")
    yield client

    clear_providers()
    web_server.app.state.bound_host = prev_host
    web_server.app.state.bound_port = prev_port
    web_server.app.state.auth_required = prev_required
    if hasattr(web_server, "set_startup_ready"):
        web_server.set_startup_ready(prev_startup_ready)


class TestDashboardStartupGracePeriod:
    """Tests for 503 response during server initialization."""

    def test_default_config_has_startup_grace_seconds(self):
        assert "startup_grace_seconds" in DEFAULT_CONFIG["dashboard"]
        assert DEFAULT_CONFIG["dashboard"]["startup_grace_seconds"] == 30.0

    def test_request_during_init_returns_503_with_retry_after(self, gated_client):
        web_server.set_startup_ready(False)

        response = gated_client.get("/api/sessions")
        assert response.status_code == 503
        assert response.headers.get("retry-after") == "3"
        data = response.json()
        assert data.get("error") == "server_initializing"
        assert "session_expired" not in response.text
        assert "unauthenticated" not in response.text

    def test_gated_request_with_valid_token_during_init_returns_503(self, gated_client):
        web_server.set_startup_ready(False)

        response = gated_client.get(
            "/api/sessions",
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        )
        assert response.status_code == 503
        assert response.headers.get("retry-after") == "3"
        data = response.json()
        assert data.get("error") == "server_initializing"

    def test_public_routes_pass_through_during_init(self, gated_client):
        web_server.set_startup_ready(False)

        # /api/auth/providers is public bootstrap
        response = gated_client.get("/api/auth/providers")
        assert response.status_code == 200

        # /login is public
        login_resp = gated_client.get("/login")
        assert login_resp.status_code == 200

        # /api/status is also public liveness probe
        status_resp = gated_client.get("/api/status")
        assert status_resp.status_code == 200

    def test_request_after_init_normal_auth_responses(self, gated_client):
        web_server.set_startup_ready(True)

        # Invalid/missing credentials -> 401
        response = gated_client.get("/api/auth/me")
        assert response.status_code == 401

        # Valid login round-trip
        r1 = gated_client.get("/auth/login?provider=stub", follow_redirects=False)
        assert r1.status_code == 302
        state = r1.headers["location"].split("state=")[1]
        cb_resp = gated_client.get(
            f"/auth/callback?code=stub_code&state={state}",
            follow_redirects=False,
        )
        assert cb_resp.status_code == 302

        # After auth, request succeeds (200)
        me_resp = gated_client.get("/api/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["user_id"] == "stub-user-1"

    def test_grace_period_ceiling_expires(self, gated_client, monkeypatch):
        web_server.set_startup_ready(False)

        # Simulate that startup started 35 seconds ago (past 30s ceiling)
        past_time = time.monotonic() - 35.0
        monkeypatch.setattr(web_server, "_startup_start_monotonic", past_time)
        if hasattr(web_server.app.state, "startup_start_monotonic"):
            web_server.app.state.startup_start_monotonic = past_time

        # Since grace ceiling expired, normal auth runs -> 401
        response = gated_client.get("/api/auth/me")
        assert response.status_code == 401


class TestLoopbackStartupGracePeriod:
    """Tests for 503 response in loopback mode during server initialization."""

    def test_loopback_request_during_init_returns_503(self):
        prev_required = getattr(web_server.app.state, "auth_required", None)
        web_server.app.state.auth_required = False
        prev_startup_ready = getattr(web_server, "_startup_ready", True)
        web_server.set_startup_ready(False)

        try:
            client = TestClient(web_server.app)
            # Protected API route without valid session token
            resp = client.get("/api/sessions")
            assert resp.status_code == 503
            assert resp.headers.get("retry-after") == "3"
            assert resp.json().get("error") == "server_initializing"

            # Public route succeeds
            pub_resp = client.get("/api/status")
            assert pub_resp.status_code == 200

            # After startup ready, returns 401
            web_server.set_startup_ready(True)
            post_resp = client.get("/api/sessions")
            assert post_resp.status_code == 401

            # With valid session token, passes through
            client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
            authed_resp = client.get("/api/sessions")
            assert authed_resp.status_code != 401
        finally:
            web_server.app.state.auth_required = prev_required
            web_server.set_startup_ready(prev_startup_ready)

    def test_loopback_valid_token_during_init_returns_503(self):
        prev_required = getattr(web_server.app.state, "auth_required", None)
        web_server.app.state.auth_required = False
        prev_startup_ready = getattr(web_server, "_startup_ready", True)
        web_server.set_startup_ready(False)

        try:
            client = TestClient(web_server.app)
            # Even with valid token, during init protected routes receive 503
            resp = client.get(
                "/api/sessions",
                headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
            )
            assert resp.status_code == 503
            assert resp.headers.get("retry-after") == "3"
        finally:
            web_server.app.state.auth_required = prev_required
            web_server.set_startup_ready(prev_startup_ready)


class TestConcurrentSessionTokenResolution:
    """Tests for concurrency and file permission hardening (Reviewer C1 & C2 findings)."""

    def test_concurrent_first_boot_resolves_identical_token(self, monkeypatch, tmp_path):
        import concurrent.futures

        test_home = tmp_path / "concurrent_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(web_server._resolve_session_token) for _ in range(4)]
            tokens = [f.result(timeout=5) for f in futures]

        token_file = test_home / ".dashboard_session_token"
        assert token_file.is_file()
        persisted = token_file.read_text(encoding="utf-8").strip()

        # Every thread must get the exact same token matching disk
        assert len(set(tokens)) == 1
        assert tokens[0] == persisted

    def test_insecure_file_permissions_repaired(self, monkeypatch, tmp_path):
        test_home = tmp_path / "repair_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        token_file.write_text("pre-existing-token\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(token_file, 0o666)  # deliberately loose permissions

        token = web_server._resolve_session_token()
        assert token == "pre-existing-token"

        if os.name != "nt":
            mode = token_file.stat().st_mode & 0o777
            assert mode == 0o600

    def test_concurrent_race_barrier_returns_persisted_token(self, monkeypatch, tmp_path):
        """Simulate synchronized publication barrier across multiple threads."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        test_home = tmp_path / "barrier_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        barrier = threading.Barrier(2)
        real_replace = os.replace

        def synchronized_replace(src, dst):
            barrier.wait(timeout=10)
            return real_replace(src, dst)

        with patch.object(web_server.os, "replace", side_effect=synchronized_replace):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(web_server._resolve_session_token) for _ in range(2)]
                tokens = [f.result(timeout=15) for f in futures]

        token_file = test_home / ".dashboard_session_token"
        assert token_file.is_file()
        persisted = token_file.read_text(encoding="utf-8").strip()

        assert len(set(tokens)) == 1
        assert tokens[0] == persisted
        assert tokens[1] == persisted

    @pytest.mark.windows_only
    def test_windows_acl_restricts_permissions(self, monkeypatch, tmp_path):
        """Verify Windows DACL restricts access to service user with inheritance removed."""
        import subprocess

        test_home = tmp_path / "win_acl_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token = web_server._resolve_session_token()
        token_file = test_home / ".dashboard_session_token"
        assert token_file.is_file()

        res = subprocess.run(
            ["icacls", str(token_file)],
            capture_output=True,
            text=True,
            check=True,
        )
        output = res.stdout.lower()
        username = (os.environ.get("USERNAME") or "").lower()
        if username:
            assert username in output
        assert "everyone" not in output

    @pytest.mark.linux_only
    def test_unix_permissions_0600_enforced(self, monkeypatch, tmp_path):
        """Verify Unix mode 0600 on created token file."""
        test_home = tmp_path / "unix_mode_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token = web_server._resolve_session_token()
        token_file = test_home / ".dashboard_session_token"
        assert token_file.is_file()
        mode = token_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_isolated_startup_lifecycle_transition(self, gated_client):
        """Demonstrate real startup transition from unready 503 to ready 200/401."""
        web_server.set_startup_ready(False)
        # Phase 1: During startup unready window, protected route returns 503
        resp1 = gated_client.get("/api/sessions")
        assert resp1.status_code == 503
        assert resp1.headers.get("retry-after") == "3"

        # Phase 2: Server finishes startup initialization
        web_server.set_startup_ready(True)
        # Phase 3: Post-initialization, returns normal auth rejection (401)
        resp2 = gated_client.get("/api/sessions")
        assert resp2.status_code == 401


