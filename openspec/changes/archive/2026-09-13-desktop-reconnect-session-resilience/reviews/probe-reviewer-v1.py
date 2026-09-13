"""Read-only implementation review; all token I/O uses temporary homes.
Run from repository root with .venv/Scripts/python.exe <this file>.
No service startup, lifecycle calls, or production config reads.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
# Retain only OS/runtime location variables before importing application code.
allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"}
for key in list(os.environ):
    if key.upper() not in allowed:
        del os.environ[key]

with tempfile.TemporaryDirectory(prefix="reviewer-resilience-") as scratch:
    os.environ["HERMES_HOME"] = scratch
    os.environ["HERMES_TEST_ISOLATION"] = scratch
    from hermes_cli import web_server as ws
    from fastapi.testclient import TestClient

    result = {}
    # Fresh, never-used token path. Barrier forces both writers to reach
    # publication after writing the same fixed .tmp path.
    race_home = Path(scratch) / "race"
    race_home.mkdir()
    os.environ["HERMES_HOME"] = str(race_home)
    barrier = threading.Barrier(2)
    real_replace = os.replace
    def synchronized_replace(src, dst):
        barrier.wait(timeout=10)
        return real_replace(src, dst)
    with patch.object(ws.os, "replace", side_effect=synchronized_replace):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ws._resolve_session_token) for _ in range(2)]
            tokens = [f.result(timeout=15) for f in futures]
    persisted = (race_home / ".dashboard_session_token").read_text().strip()
    result["concurrent_first_boot"] = {
        "distinct_returned_tokens": len(set(tokens)),
        "callers_matching_persisted_token": sum(t == persisted for t in tokens),
        "total_callers": len(tokens),
    }
    os.environ["HERMES_HOME"] = scratch
    ws.app.state.auth_required = False
    ws.set_startup_ready(False)
    client = TestClient(ws.app)  # no lifespan -> no background services
    unauthorized = client.get("/api/sessions")
    authorized = client.get("/api/sessions", headers={ws._SESSION_HEADER_NAME: ws._SESSION_TOKEN})
    result["loopback_during_initialization"] = {
        "missing_token_status": unauthorized.status_code,
        "valid_token_status": authorized.status_code,
        "ready": ws.is_startup_ready(ws.app.state),
    }
    ws.set_startup_ready(True)
    result["loopback_after_initialization"] = {
        "invalid_token_status": client.get("/api/sessions", headers={ws._SESSION_HEADER_NAME: "invalid-test-token"}).status_code,
        "valid_token_status": client.get("/api/sessions", headers={ws._SESSION_HEADER_NAME: ws._SESSION_TOKEN}).status_code,
    }
    print(json.dumps(result, indent=2))
