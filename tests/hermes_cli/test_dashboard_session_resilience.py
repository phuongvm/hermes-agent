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

    def test_delayed_writer_does_not_overwrite_persisted_token(self, monkeypatch, tmp_path):
        """Simulate delayed writer attempting publication after first resolver has returned."""
        test_home = tmp_path / "delayed_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        # First resolver runs to completion, creating and persisting a valid token
        token1 = web_server._resolve_session_token()
        token_file = test_home / ".dashboard_session_token"
        assert token_file.is_file()
        assert token_file.read_text(encoding="utf-8").strip() == token1

        # Now simulate a delayed writer that prepared a different token in a tmp file
        delayed_token = "delayed-candidate-token-that-must-not-overwrite"
        tmp_file = test_home / ".dashboard_session_token.tmp.delayed"
        tmp_file.write_text(delayed_token + "\n", encoding="utf-8")

        # Attempt to publish the delayed token file to the existing token path
        won = web_server._publish_session_token(tmp_file, token_file)

        # Must NOT win publication (single-winner semantics)
        assert won is False
        # Tmp file must be cleaned up
        assert not tmp_file.exists()
        # Persisted file must still contain the original token1
        assert token_file.read_text(encoding="utf-8").strip() == token1

        # Subsequent resolver returns the original token1
        token2 = web_server._resolve_session_token()
        assert token2 == token1

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

    @pytest.mark.windows_only
    def test_windows_acl_repair_removes_explicit_everyone(self, monkeypatch, tmp_path):
        """Verify explicit Everyone grant on existing token file is removed upon repair."""
        import subprocess

        test_home = tmp_path / "win_repair_acl"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        token_file.write_text("pre-existing-token-for-acl-test\n", encoding="utf-8")
        subprocess.run(["icacls", str(token_file), "/grant", "Everyone:(R)"], check=True, capture_output=True)

        chk_before = subprocess.run(["icacls", str(token_file)], capture_output=True, text=True, check=True).stdout.lower()
        assert "everyone:(" in chk_before

        token = web_server._resolve_session_token()
        assert token == "pre-existing-token-for-acl-test"

        chk_after = subprocess.run(["icacls", str(token_file)], capture_output=True, text=True, check=True).stdout.lower()
        # Ensure no Everyone ACE remains in DACL
        assert "everyone:(" not in chk_after
        assert "*s-1-1-0:(" not in chk_after

    @pytest.mark.windows_only
    def test_windows_acl_failure_fails_closed_and_rejects_insecure_file(self, monkeypatch, tmp_path):
        """Verify failure in icacls causes resolver to reject insecure file and fail closed."""
        import subprocess

        test_home = tmp_path / "win_acl_fail_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        token_file.write_text("insecure-secret-token\n", encoding="utf-8")

        real_run = subprocess.run

        def fail_icacls(args, **kwargs):
            if args and args[0] == "icacls":
                return subprocess.CompletedProcess(args, 5, b"", b"injected ACL access denied")
            return real_run(args, **kwargs)

        with patch.object(subprocess, "run", side_effect=fail_icacls):
            token = web_server._resolve_session_token()

        assert token != "insecure-secret-token"
        assert len(token) >= 32

    @pytest.mark.windows_only
    def test_pre_write_temporary_file_is_secured_before_secret_written(self, monkeypatch, tmp_path):
        """Verify temporary token file is secured with restricted DACL while empty before secret bytes are written."""
        test_home = tmp_path / "win_pre_write_acl"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        recorded_states = []
        real_secure = web_server._secure_token_file

        def hook_secure(path):
            ok = real_secure(path)
            if ".tmp." in str(path):
                recorded_states.append((path.stat().st_size, ok))
            return ok

        with patch.object(web_server, "_secure_token_file", side_effect=hook_secure):
            token = web_server._resolve_session_token()

        assert len(recorded_states) >= 1
        # Must be empty (0 bytes) when secured before secret is written
        assert recorded_states[0][0] == 0
        assert recorded_states[0][1] is True

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
        """Demonstrate startup transition from unready 503 to ready 200/401."""
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

    @pytest.mark.windows_only
    def test_windows_acl_repair_removes_arbitrary_principal_grant(self, monkeypatch, tmp_path):
        """Verify Windows ACL repair strips arbitrary principal/SID (e.g. S-1-5-19 LOCAL SERVICE) from DACL."""
        import subprocess

        test_home = tmp_path / "win_arbitrary_sid_acl"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        token_file.write_text("pre-existing-token-for-arbitrary-sid\n", encoding="utf-8")
        # Add explicit grant to LOCAL SERVICE SID
        subprocess.run(["icacls", str(token_file), "/grant", "*S-1-5-19:(R)"], check=True, capture_output=True)

        chk_before = subprocess.run(["icacls", str(token_file)], capture_output=True, text=True, check=True).stdout
        assert "LOCAL SERVICE" in chk_before or "S-1-5-19" in chk_before

        token = web_server._resolve_session_token()
        assert token == "pre-existing-token-for-arbitrary-sid"

        chk_after = subprocess.run(["icacls", str(token_file)], capture_output=True, text=True, check=True).stdout
        assert "LOCAL SERVICE" not in chk_after
        assert "S-1-5-19" not in chk_after

    @pytest.mark.windows_only
    def test_windows_acl_rejects_unauthorized_principal_when_unrepairable(self, monkeypatch, tmp_path):
        """Verify resolver fails closed and rejects file if an unauthorized principal remains in DACL."""
        import subprocess

        test_home = tmp_path / "win_unrepairable_acl"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        token_file.write_text("insecure-unrepairable-token\n", encoding="utf-8")

        real_run = subprocess.run

        def simulate_stubborn_unauthorized_ace(args, **kwargs):
            res = real_run(args, **kwargs)
            # When icacls is called for verification in Step 3, pretend an unauthorized ACE remains
            if args and args[0] == "icacls" and len(args) == 2:
                mock_out = f"{token_file} NT AUTHORITY\\ANONYMOUS LOGON:(R)\nSuccessfully processed 1 files"
                return subprocess.CompletedProcess(args, 0, mock_out, "")
            return res

        with patch.object(subprocess, "run", side_effect=simulate_stubborn_unauthorized_ace):
            token = web_server._resolve_session_token()

        assert token != "insecure-unrepairable-token"
        assert len(token) >= 32

    def test_lock_acquisition_failure_on_corrupt_file_does_not_destroy_disk(self, monkeypatch, tmp_path):
        """Verify lock acquisition failure on corrupted file fails closed without unlinking or destroying disk."""
        test_home = tmp_path / "lock_fail_corrupt_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        corrupt_bytes = b"\xff\xfe\x00\x01\x80\x90"
        token_file.write_bytes(corrupt_bytes)

        class _FailingLock:
            def __init__(self, *args, **kwargs):
                self.acquired = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with patch.object(web_server, "_SessionTokenLock", _FailingLock):
            token = web_server._resolve_session_token()

        # Lock failed, so resolver must NOT destroy disk or perform unverified regeneration
        assert token_file.is_file()
        assert token_file.read_bytes() == corrupt_bytes
        # Returns an ephemeral token
        assert len(token) >= 32

    def test_lock_acquisition_failure_reads_published_token_if_available(self, monkeypatch, tmp_path):
        """Verify caller unable to acquire lock still converges on published valid token without modifying disk."""
        test_home = tmp_path / "lock_fail_published_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        token_file.write_text("published-winner-token\n", encoding="utf-8")
        web_server._secure_token_file(token_file)

        class _FailingLock:
            def __init__(self, *args, **kwargs):
                self.acquired = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with patch.object(web_server, "_SessionTokenLock", _FailingLock):
            token = web_server._resolve_session_token()

        assert token == "published-winner-token"
        assert token_file.read_text(encoding="utf-8").strip() == "published-winner-token"

    @pytest.mark.parametrize("scenario", ["missing", "empty", "corrupt"])
    def test_cross_process_session_token_convergence(self, tmp_path, scenario):
        """Verify 4 separate OS subprocesses resolving token concurrently converge on identical disk token."""
        import hashlib
        import subprocess
        import sys

        test_home = tmp_path / f"cross_proc_{scenario}"
        test_home.mkdir()
        token_file = test_home / ".dashboard_session_token"

        if scenario == "empty":
            token_file.write_text("", encoding="utf-8")
        elif scenario == "corrupt":
            token_file.write_bytes(b"\xff\xfe\x00\x01\x80\x90")

        worker_script = tmp_path / "worker.py"
        worker_script.write_text(
            "import os, sys, time, hashlib\n"
            "from pathlib import Path\n"
            "home = sys.argv[1]\n"
            "worker_id = sys.argv[2]\n"
            "os.environ['HERMES_HOME'] = home\n"
            "os.environ['HERMES_DASHBOARD_SESSION_TOKEN'] = 'import-barrier-fixture'\n"
            "from hermes_cli import web_server as ws\n"
            "del os.environ['HERMES_DASHBOARD_SESSION_TOKEN']\n"
            "Path(home, f'ready_{worker_id}').touch()\n"
            "barrier = Path(home, 'barrier_go')\n"
            "deadline = time.monotonic() + 20\n"
            "while not barrier.exists():\n"
            "    if time.monotonic() > deadline:\n"
            "        raise TimeoutError('barrier timeout')\n"
            "    time.sleep(0.02)\n"
            "token = ws._resolve_session_token()\n"
            "print(hashlib.sha256(token.strip().encode('utf-8')).hexdigest())\n",
            encoding="utf-8",
        )

        num_workers = 4
        procs = [
            subprocess.Popen(
                [sys.executable, str(worker_script), str(test_home), str(i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
            for i in range(num_workers)
        ]

        try:
            ready_deadline = time.monotonic() + 25
            while len(list(test_home.glob("ready_*"))) < num_workers:
                if time.monotonic() > ready_deadline:
                    raise TimeoutError("Workers did not become ready in time")
                time.sleep(0.05)

            (test_home / "barrier_go").touch()

            outputs = [p.communicate(timeout=30) for p in procs]
            exit_codes = [p.returncode for p in procs]
            assert exit_codes == [0] * num_workers, f"Workers failed: {outputs}"

            hashes = [out.strip() for out, err in outputs]
            assert len(set(hashes)) == 1, f"Divergent token hashes: {hashes}"

            assert token_file.is_file()
            disk_token = token_file.read_text(encoding="utf-8").strip()
            disk_hash = hashlib.sha256(disk_token.encode("utf-8")).hexdigest()
            assert hashes[0] == disk_hash
        finally:
            for p in procs:
                if p.poll() is None:
                    p.terminate()
                    p.wait(timeout=5)

    def test_production_server_startup_completion_on_ephemeral_port(self, monkeypatch, tmp_path):
        """Verify production _on_server_started completion path transitions server to ready without manual toggles."""
        import asyncio
        import httpx
        import uvicorn
        import threading

        test_home = tmp_path / "prod_startup_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        web_server.set_startup_ready(False)
        prev_required = getattr(web_server.app.state, "auth_required", None)
        web_server.app.state.auth_required = False

        bound_info = {"port": None}
        server_ready_event = threading.Event()

        config = uvicorn.Config(
            web_server.app,
            host="127.0.0.1",
            port=0,
            log_level="critical",
            lifespan="on",
        )
        server = uvicorn.Server(config)

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _serve():
                if not config.loaded:
                    config.load()
                server.lifespan = config.lifespan_class(config)
                with server.capture_signals():
                    await server.startup()
                    if server.should_exit:
                        return

                    # Execute production _on_server_started hook directly!
                    web_server._on_server_started(
                        server,
                        host="127.0.0.1",
                        port=0,
                        headless=True,
                        open_browser=False,
                        initial_profile="",
                        start_mcp_discovery_after_bind=False,
                    )
                    bound_info["port"] = web_server.app.state.bound_port
                    server_ready_event.set()

                    await server.main_loop()
                    if server.started:
                        await server.shutdown()

            loop.run_until_complete(_serve())

        t = threading.Thread(target=run_server, daemon=True)
        t.start()

        try:
            assert server_ready_event.wait(timeout=15), "Server did not bind within 15s"
            bound_port = bound_info["port"]
            assert bound_port is not None and bound_port > 0

            # Verify production hook marked readiness True
            assert web_server.is_startup_ready(web_server.app.state) is True

            # Post-startup: protected route returns 401 without token and 200 with token
            r1 = httpx.get(f"http://127.0.0.1:{bound_port}/api/sessions", timeout=5)
            assert r1.status_code == 401

            r2 = httpx.get(
                f"http://127.0.0.1:{bound_port}/api/sessions",
                headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
                timeout=5,
            )
            assert r2.status_code == 200
        finally:
            server.should_exit = True
            t.join(timeout=5)
            web_server.app.state.auth_required = prev_required
            web_server.set_startup_ready(True)

    def test_controlled_transport_startup_grace_period_transition(self, monkeypatch, tmp_path):
        """Verify HTTP 503 response and 401/200 transition over a real loopback socket in a controlled startup simulation.

        Note on listener and readiness architecture:
        In production (web_server.start_server), uvicorn's startup sequence executes _on_server_started synchronously
        immediately after `await server.startup()` and before entering `server.main_loop()`.
        This test exercises the real middleware and transport socket boundary under a controlled event-scheduled
        initialization task. It is a controlled transport isolation test demonstrating 503 Retry-After handling
        and post-initialization auth, not an unisolated production end-to-end startup test.
        Server threads and events are strictly isolated and cleaned up upon test completion.
        """
        import asyncio
        import httpx
        import uvicorn
        import threading

        test_home = tmp_path / "grace_transition_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        web_server.set_startup_ready(False)
        prev_required = getattr(web_server.app.state, "auth_required", None)
        web_server.app.state.auth_required = False

        bound_info = {"port": None}
        server_listening_event = threading.Event()
        init_work_done = threading.Event()

        config = uvicorn.Config(
            web_server.app,
            host="127.0.0.1",
            port=0,
            log_level="critical",
            lifespan="on",
        )
        server = uvicorn.Server(config)

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _serve():
                if not config.loaded:
                    config.load()
                server.lifespan = config.lifespan_class(config)
                with server.capture_signals():
                    await server.startup()
                    if server.should_exit:
                        return
                    sock = server.servers[0].sockets[0]
                    bound_info["port"] = sock.getsockname()[1]

                    # Server is listening, but startup work is pending
                    web_server.set_startup_ready(False, web_server.app.state)

                    async def _complete_startup_when_signaled():
                        await loop.run_in_executor(None, init_work_done.wait)
                        web_server._on_server_started(
                            server,
                            host="127.0.0.1",
                            port=bound_info["port"],
                            headless=True,
                            open_browser=False,
                            initial_profile="",
                            start_mcp_discovery_after_bind=False,
                        )

                    asyncio.create_task(_complete_startup_when_signaled())
                    server_listening_event.set()

                    await server.main_loop()
                    if server.started:
                        await server.shutdown()

            loop.run_until_complete(_serve())

        t = threading.Thread(target=run_server, daemon=True)
        t.start()

        try:
            assert server_listening_event.wait(timeout=15), "Server did not bind within 15s"
            bound_port = bound_info["port"]
            assert bound_port is not None and bound_port > 0

            # Phase 1: While startup work is pending, requests return 503 (both unauthenticated and authenticated)
            r1_unauth = httpx.get(f"http://127.0.0.1:{bound_port}/api/sessions", timeout=5)
            assert r1_unauth.status_code == 503
            assert r1_unauth.headers.get("retry-after") == "3"
            assert r1_unauth.json().get("error") == "server_initializing"

            r1_auth = httpx.get(
                f"http://127.0.0.1:{bound_port}/api/sessions",
                headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
                timeout=5,
            )
            assert r1_auth.status_code == 503
            assert r1_auth.headers.get("retry-after") == "3"
            assert r1_auth.json().get("error") == "server_initializing"

            # Phase 2: Signal completion of startup work via event (no arbitrary timer)
            init_work_done.set()

            # Wait for production hook to set readiness
            for _ in range(100):
                if web_server.is_startup_ready(web_server.app.state):
                    break
                time.sleep(0.05)
            assert web_server.is_startup_ready(web_server.app.state)

            # Phase 3: Post-startup, normal auth returns 401 without token and 200 with token
            r2 = httpx.get(f"http://127.0.0.1:{bound_port}/api/sessions", timeout=5)
            assert r2.status_code == 401

            r3 = httpx.get(
                f"http://127.0.0.1:{bound_port}/api/sessions",
                headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
                timeout=5,
            )
            assert r3.status_code == 200
        finally:
            init_work_done.set()
            server.should_exit = True
            t.join(timeout=5)
            web_server.app.state.auth_required = prev_required
            web_server.set_startup_ready(True)

    @pytest.mark.windows_only
    def test_native_win32_process_identity_direct_branch(self):
        """Verify primary Win32 ctypes branch resolves SID and account name without whoami fallback."""
        import subprocess

        whoami_calls = []
        real_run = subprocess.run

        def intercept_run(args, **kwargs):
            if args and args[0] == "whoami":
                whoami_calls.append(True)
            return real_run(args, **kwargs)

        with patch.object(subprocess, "run", side_effect=intercept_run):
            sid, name = web_server._get_effective_user_identity()

        assert sid is not None and sid.startswith("S-1-")
        assert name is not None and len(name) > 0
        assert len(whoami_calls) == 0, f"Expected 0 whoami fallback calls, got {len(whoami_calls)}"

    @pytest.mark.windows_only
    def test_native_whoami_fallback_when_ctypes_fails(self):
        """Verify whoami /user fallback succeeds and resolves SID when ctypes Win32 lookup fails."""
        import ctypes

        with patch.object(ctypes, "WinDLL", side_effect=OSError("injected ctypes failure")):
            sid, name = web_server._get_effective_user_identity()

        assert sid is not None and sid.startswith("S-1-")
        assert name is not None and len(name) > 0

    @pytest.mark.windows_only
    def test_native_identity_failure_fails_closed_without_env_fallback(self, monkeypatch):
        """Verify native identity failure returns (None, None) and never authorizes untrusted USERNAME env."""
        import ctypes
        import subprocess

        monkeypatch.setenv("USERNAME", "Everyone")
        real_run = subprocess.run

        def fail_whoami(args, **kwargs):
            if args and args[0] == "whoami":
                raise OSError("injected whoami failure")
            return real_run(args, **kwargs)

        with patch.object(ctypes, "WinDLL", side_effect=OSError("injected ctypes failure")), patch.object(
            subprocess, "run", side_effect=fail_whoami
        ):
            sid, name = web_server._get_effective_user_identity()

        assert sid is None
        assert name is None

    @pytest.mark.windows_only
    def test_windows_native_identity_failure_preserves_acl_and_rejects_credential(self, monkeypatch, tmp_path):
        """Verify native identity failure rejects token file, preserves existing DACL/content, and does not grant Everyone."""
        import ctypes
        import subprocess

        test_home = tmp_path / "win_identity_fail_preserve_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        token_file.write_text("secret-token-to-preserve\n", encoding="utf-8")
        assert web_server._secure_token_file(token_file)

        real_run = subprocess.run
        initial_acl = real_run(["icacls", str(token_file)], capture_output=True, text=True, check=True).stdout

        def fail_whoami(args, **kwargs):
            if args and args[0] == "whoami":
                raise OSError("injected whoami failure")
            return real_run(args, **kwargs)

        with patch.object(ctypes, "WinDLL", side_effect=OSError("injected ctypes failure")), patch.object(
            subprocess, "run", side_effect=fail_whoami
        ), patch.dict(os.environ, {"USERNAME": "Everyone"}):
            # 1. _secure_token_file must fail closed
            secured = web_server._secure_token_file(token_file)
            assert secured is False

            # 2. Disk DACL must be preserved and must NOT have Everyone grant
            current_acl = real_run(["icacls", str(token_file)], capture_output=True, text=True, check=True).stdout
            assert "everyone:(" not in current_acl.lower()
            assert "*s-1-1-0:(" not in current_acl.lower()

            # 3. _resolve_session_token must reject the credential file and return ephemeral token
            resolved = web_server._resolve_session_token()
            assert resolved != "secret-token-to-preserve"
            assert len(resolved) >= 32

            # 4. Token file content must remain preserved
            assert token_file.read_text(encoding="utf-8") == "secret-token-to-preserve\n"

    @pytest.mark.windows_only
    def test_windows_native_identity_failure_when_creating_token_publishes_no_secrets(self, monkeypatch, tmp_path):
        """Verify native identity failure during token creation does not publish secret token file to disk."""
        import ctypes
        import subprocess

        test_home = tmp_path / "win_identity_fail_create_home"
        test_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(test_home))
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)

        token_file = test_home / ".dashboard_session_token"
        real_run = subprocess.run

        def fail_whoami(args, **kwargs):
            if args and args[0] == "whoami":
                raise OSError("injected whoami failure")
            return real_run(args, **kwargs)

        with patch.object(ctypes, "WinDLL", side_effect=OSError("injected ctypes failure")), patch.object(
            subprocess, "run", side_effect=fail_whoami
        ), patch.dict(os.environ, {"USERNAME": "Everyone"}):
            resolved = web_server._resolve_session_token()
            assert len(resolved) >= 32
            # Token file must NOT be published on disk
            assert not token_file.exists()
