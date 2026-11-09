"""Unit, state-machine, and normative benchmark tests for /api/status single-pass
state.db probing, stat-keyed config caching, and StatusSnapshotProvider singleflight/SWR.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import gateway.readiness as _gw_readiness
import gateway.status as _gw_status
import hermes_cli.config as _cli_config
import hermes_cli.web_server_gateway as _ws_gateway
from hermes_cli.web_routers import status as status_router


_SCHEMA_SQL = """
CREATE TABLE state_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    title TEXT,
    cwd TEXT,
    parent_session_id TEXT,
    last_activity_at REAL,
    archived INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_sessions_effective_activity
ON sessions(COALESCE(last_activity_at, started_at) DESC)
WHERE parent_session_id IS NULL AND COALESCE(hidden, 0) = 0;
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    timestamp REAL NOT NULL
);
CREATE INDEX idx_messages_session ON messages(session_id, timestamp);
"""


def _seed_state_db(
    db_path: Path,
    *,
    now: float,
    ended_newer_count: int = 50,
    active_older_count: int = 6,
    messages_count: int = 100,
    fts_high_water: int | None = None,
    fts_progress: int | None = None,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_SCHEMA_SQL)
        if fts_high_water is not None:
            conn.execute(
                "INSERT INTO state_meta (key, value) VALUES (?, ?)",
                ("fts_rebuild_high_water", str(fts_high_water)),
            )
        if fts_progress is not None:
            conn.execute(
                "INSERT INTO state_meta (key, value) VALUES (?, ?)",
                ("fts_rebuild_progress", str(fts_progress)),
            )

        # Insert 6 long-lived active root sessions started yesterday (older started_at than the
        # 50 newest ended sessions) but with recent last_activity_at within the 300s window.
        for i in range(active_older_count):
            sid = f"active_old_{i}"
            conn.execute(
                "INSERT INTO sessions (id, source, started_at, ended_at, parent_session_id, last_activity_at, hidden) "
                "VALUES (?, 'desktop', ?, NULL, NULL, ?, 0)",
                (sid, now - 86400.0 - i * 60.0, now - 30.0 - i * 10.0),
            )

        # Insert a child session and a hidden session that must be excluded by the predicate.
        conn.execute(
            "INSERT INTO sessions (id, source, started_at, ended_at, parent_session_id, last_activity_at, hidden) "
            "VALUES ('child_active', 'desktop', ?, NULL, 'active_old_0', ?, 0)",
            (now - 20.0, now - 10.0),
        )
        conn.execute(
            "INSERT INTO sessions (id, source, started_at, ended_at, parent_session_id, last_activity_at, hidden) "
            "VALUES ('hidden_active', 'desktop', ?, NULL, NULL, ?, 1)",
            (now - 20.0, now - 10.0),
        )

        # Insert 50+ newer sessions that have already ended (or are inactive).
        for i in range(ended_newer_count):
            sid = f"ended_new_{i}"
            conn.execute(
                "INSERT INTO sessions (id, source, started_at, ended_at, parent_session_id, last_activity_at, hidden) "
                "VALUES (?, 'cli', ?, ?, NULL, ?, 0)",
                (sid, now - 500.0 + i, now - 400.0 + i, now - 400.0 + i),
            )

        # Populate messages table.
        msg_rows = [
            (f"active_old_{i % max(1, active_older_count)}", "user", f"message body {i} " * 8, now - i)
            for i in range(messages_count)
        ]
        conn.executemany(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            msg_rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _reset_caches():
    if hasattr(status_router, "_reset_status_snapshot_cache"):
        status_router._reset_status_snapshot_cache()
    yield
    if hasattr(status_router, "_reset_status_snapshot_cache"):
        status_router._reset_status_snapshot_cache()


def _configure_isolated_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    cfg_path = home / "config.yaml"
    if not cfg_path.exists():
        cfg_path.write_text("model: cb-gemini-argon\ngateway:\n  platforms: {}\n", encoding="utf-8")
    current_pid = os.getpid()
    try:
        import psutil

        create_time = psutil.Process(current_pid).create_time()
    except Exception:
        create_time = 100.0
    from datetime import UTC, datetime

    now_iso = datetime.now(UTC).isoformat()
    gw_state = home / "gateway_state.json"
    if not gw_state.exists():
        gw_state.write_text(
            json.dumps(
                {
                    "gateway_state": "running",
                    "pid": current_pid,
                    "start_time": create_time,
                    "updated_at": now_iso,
                    "platforms": {},
                    "active_agents": 2,
                }
            ),
            encoding="utf-8",
        )
    gw_pid_file = home / "gateway.pid"
    if not gw_pid_file.exists():
        gw_pid_file.write_text(f"{current_pid}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_process_hermes_home", lambda: home)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    monkeypatch.setattr(_cli_config, "get_hermes_home", lambda: home)
    monkeypatch.setattr(_cli_config, "get_config_path", lambda: home / "config.yaml")
    monkeypatch.setattr(_cli_config, "get_env_path", lambda: home / ".env")
    monkeypatch.setattr(status_router, "get_hermes_home", lambda: home)
    monkeypatch.setattr(status_router, "get_config_path", lambda: home / "config.yaml")
    monkeypatch.setattr(status_router, "get_env_path", lambda: home / ".env")
    monkeypatch.setattr(_gw_status, "get_running_pid_cached", lambda *args, **kwargs: 12345)
    monkeypatch.setattr(
        _ws_gateway,
        "_collect_profile_gateway_topology_cached",
        lambda: {
            "profiles": ["default"],
            "parked_profiles": [],
            "gateway_mode": "single",
            "multiplex_standalone_reason": None,
            "gateways": [{"profile": "default", "ports": {}}],
            "profile_platforms": {},
        },
    )


def test_probe_state_db_status_counts_long_lived_active_sessions_beyond_50_newest_rows(tmp_path):
    now = 1_793_960_000.0
    db_path = tmp_path / "state.db"
    _seed_state_db(
        db_path,
        now=now,
        ended_newer_count=55,
        active_older_count=6,
        messages_count=120,
        fts_high_water=200,
        fts_progress=50,
    )

    res = status_router.probe_state_db_status(db_path, active_window_s=300.0, now=now)
    assert res["active_sessions"] == 6
    assert res["storage_component"] == {"status": "ok"}
    assert res["fts_rebuild"] == {"pending": True, "total": 200, "indexed": 50, "percent": 25}


@pytest.mark.asyncio
async def test_get_status_opens_sqlite_at_most_once_across_active_sessions_fts_and_storage(
    tmp_path, monkeypatch
):
    now = time.time()
    _configure_isolated_home(monkeypatch, tmp_path)
    _seed_state_db(
        tmp_path / "state.db",
        now=now,
        ended_newer_count=55,
        active_older_count=6,
        messages_count=80,
        fts_high_water=400,
        fts_progress=100,
    )

    status_router._reset_status_snapshot_cache()
    real_connect = sqlite3.connect
    connect_calls: list[str] = []
    executed_queries: list[str] = []

    def _spy_connect(database, *args, **kwargs):
        connect_calls.append(str(database))
        conn = real_connect(database, *args, **kwargs)
        conn.set_trace_callback(lambda q: executed_queries.append(str(q)))
        return conn

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)

    body = await status_router.get_status()
    assert len(connect_calls) == 1, f"Expected exactly 1 sqlite3.connect call, got {connect_calls}"
    assert not any("quick_check" in q.lower() for q in executed_queries), (
        f"PRAGMA quick_check must NEVER run on the status path; executed: {executed_queries}"
    )
    assert any("sqlite_master" in q.lower() for q in executed_queries)
    assert body["active_sessions"] == 6
    assert body["components"]["storage"] == {"status": "ok"}
    assert body["fts_rebuild"] == {"pending": True, "total": 400, "indexed": 100, "percent": 25}


@pytest.mark.asyncio
async def test_get_status_missing_state_db_opens_zero_connections_and_creates_no_files(
    tmp_path, monkeypatch
):
    _configure_isolated_home(monkeypatch, tmp_path)
    real_connect = sqlite3.connect
    connect_calls: list[str] = []

    def _spy_connect(database, *args, **kwargs):
        connect_calls.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)

    body = await status_router.get_status()
    assert len(connect_calls) == 0
    assert body["active_sessions"] == 0
    assert body["components"]["storage"] == {"status": "ok"}
    assert "fts_rebuild" not in body
    assert not (tmp_path / "state.db").exists()


def test_probe_state_db_status_corrupt_database_reports_degraded_corrupt(tmp_path):
    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"not a valid sqlite3 file header at all" * 4)
    res = status_router.probe_state_db_status(db_path)
    assert res["active_sessions"] == 0
    assert res["fts_rebuild"] is None
    assert res["storage_component"] == {"status": "degraded", "reason": "corrupt"}


@pytest.mark.asyncio
async def test_get_status_respects_monkeypatched_legacy_helpers(tmp_path, monkeypatch):
    _configure_isolated_home(monkeypatch, tmp_path)
    monkeypatch.setattr(status_router, "_count_status_active_sessions", lambda: 42)
    monkeypatch.setattr(
        status_router,
        "_fts_rebuild_status",
        lambda: {"pending": True, "total": 10, "indexed": 5, "percent": 50},
    )
    monkeypatch.setattr(
        _gw_readiness,
        "_probe_state_db",
        lambda _home: {"status": "degraded", "detail": "corrupt"},
    )

    body = await status_router.get_status()
    assert body["active_sessions"] == 42
    assert body["fts_rebuild"] == {"pending": True, "total": 10, "indexed": 5, "percent": 50}
    assert body["components"]["storage"] == {"status": "degraded", "reason": "corrupt"}


@pytest.mark.asyncio
async def test_stat_keyed_config_cache_reuses_unchanged_config_and_invalidates_on_edit(
    tmp_path, monkeypatch
):
    _configure_isolated_home(monkeypatch, tmp_path)
    version_calls = []
    platform_calls = []

    def _fake_check_version():
        version_calls.append(1)
        return (10, 10)

    import gateway.config as _gw_config

    class FakeGatewayConfig:
        def get_connected_platforms(self):
            platform_calls.append(1)
            return []

    monkeypatch.setattr(_cli_config, "check_config_version", _fake_check_version)
    monkeypatch.setattr(_gw_config, "load_gateway_config", lambda: FakeGatewayConfig())

    # First call populates stat-keyed cache.
    await status_router.get_status()
    assert len(version_calls) == 1
    assert len(platform_calls) == 1

    # Force snapshot TTL expiry while keeping config.yaml untouched.
    status_router.STATUS_SNAPSHOT_PROVIDER.age_entries_for_test(10.0)
    await status_router.get_status()
    assert len(version_calls) == 1
    assert len(platform_calls) == 1

    # Modify config.yaml (size + mtime_ns change) -> stat-keyed cache must reload immediately.
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "\n# updated comment changing size\n",
        encoding="utf-8",
    )
    status_router.STATUS_SNAPSHOT_PROVIDER.age_entries_for_test(10.0)
    await status_router.get_status()
    assert len(version_calls) == 2
    assert len(platform_calls) == 2


@pytest.mark.asyncio
async def test_singleflight_coalesces_8_concurrent_cold_requests_and_isolates_mutations(
    tmp_path, monkeypatch
):
    _configure_isolated_home(monkeypatch, tmp_path)
    _seed_state_db(tmp_path / "state.db", now=time.time(), ended_newer_count=10, active_older_count=3)

    calls = []
    orig_collect = status_router.STATUS_SNAPSHOT_PROVIDER._collect_fn

    async def _slow_collect(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(0.05)
        return await orig_collect(*args, **kwargs)

    monkeypatch.setattr(status_router.STATUS_SNAPSHOT_PROVIDER, "_collect_fn", _slow_collect)

    results = await asyncio.gather(*(status_router.get_status() for _ in range(8)))
    assert len(calls) == 1
    assert all(r["active_sessions"] == 3 for r in results)

    # Mutating one returned dict must not affect other callers or subsequent cached reads.
    results[0]["components"]["gateway"]["status"] = "mutated_by_caller"
    results[0]["active_sessions"] = 999
    assert results[1]["components"]["gateway"]["status"] == "ok"
    warm = await status_router.get_status()
    assert len(calls) == 1
    assert warm["active_sessions"] == 3
    assert warm["components"]["gateway"]["status"] == "ok"


@pytest.mark.asyncio
async def test_signed_out_and_authenticated_callers_share_snapshot_with_isolated_shaping(
    tmp_path, monkeypatch
):
    _configure_isolated_home(monkeypatch, tmp_path)
    _seed_state_db(tmp_path / "state.db", now=time.time(), ended_newer_count=10, active_older_count=4)

    calls = []
    orig_collect = status_router.STATUS_SNAPSHOT_PROVIDER._collect_fn

    async def _counted_collect(*args, **kwargs):
        calls.append(1)
        return await orig_collect(*args, **kwargs)

    monkeypatch.setattr(status_router.STATUS_SNAPSHOT_PROVIDER, "_collect_fn", _counted_collect)

    signed_out_flag = False
    monkeypatch.setattr(
        status_router,
        "_webapp_caller_signed_out",
        lambda _req: asyncio.sleep(0, result=signed_out_flag),
    )

    full_body = await status_router.get_status()
    signed_out_flag = True
    signed_out_body = await status_router.get_status()

    assert len(calls) == 1
    assert set(signed_out_body.keys()) <= set(status_router._WEBAPP_SIGNED_OUT_STATUS_KEYS)
    assert "active_sessions" not in signed_out_body
    assert "profiles" not in signed_out_body
    assert full_body["active_sessions"] == 4
    assert "profiles" in full_body


@pytest.mark.asyncio
async def test_cold_miss_exception_and_cold_timeout_reset_to_empty_and_allow_retry(
    tmp_path, monkeypatch
):
    _configure_isolated_home(monkeypatch, tmp_path)
    provider = status_router.STATUS_SNAPSHOT_PROVIDER
    orig_collect = provider._collect_fn

    # 1. Cold-miss exception propagates to all concurrent waiters and resets entry to EMPTY.
    async def _failing_collect(*_a, **_kw):
        await asyncio.sleep(0.02)
        raise RuntimeError("boom on cold miss")

    monkeypatch.setattr(provider, "_collect_fn", _failing_collect)
    with pytest.raises(RuntimeError, match="boom on cold miss"):
        await asyncio.gather(*(status_router.get_status() for _ in range(4)))

    entry = provider.inspect_entry_for_test(tmp_path, "")
    assert entry["state"] == "EMPTY"
    assert entry["in_flight"] is None

    # 2. Cold-miss timeout (> cold_timeout_s) increments gen, resets to EMPTY, and raises HTTP 503 / TimeoutError.
    monkeypatch.setattr(provider, "cold_timeout_s", 0.08)

    async def _hanging_collect(*_a, **_kw):
        await asyncio.sleep(0.30)
        return await orig_collect(*_a, **_kw)

    monkeypatch.setattr(provider, "_collect_fn", _hanging_collect)
    gen_before = provider.inspect_entry_for_test(tmp_path, "")["gen"]
    with pytest.raises((TimeoutError, status_router.HTTPException)):
        await status_router.get_status()
    after_timeout = provider.inspect_entry_for_test(tmp_path, "")
    assert after_timeout["gen"] > gen_before

    # Wait for the hanging task to finish and verify a clean retry succeeds.
    await asyncio.sleep(0.28)
    monkeypatch.setattr(provider, "cold_timeout_s", 10.0)
    monkeypatch.setattr(provider, "_collect_fn", orig_collect)
    recovered = await status_router.get_status()
    assert recovered["gateway_running"] is True


@pytest.mark.asyncio
async def test_stale_while_revalidate_soft_deadline_and_max_stale_eviction(tmp_path, monkeypatch):
    _configure_isolated_home(monkeypatch, tmp_path)
    _seed_state_db(tmp_path / "state.db", now=time.time(), ended_newer_count=10, active_older_count=2)
    provider = status_router.STATUS_SNAPSHOT_PROVIDER
    orig_collect = provider._collect_fn

    # Prime initial FRESH snapshot (active_sessions = 2).
    first = await status_router.get_status()
    assert first["active_sessions"] == 2

    # Age past 3.0s TTL (e.g. 4.0s old, still within MAX_STALE_S = 30.0s).
    provider.age_entries_for_test(4.0)

    # Slow refresh (2.5s > 1.5s soft deadline) updates active_sessions to 5 when it finishes.
    async def _slow_refresh(*args, **kwargs):
        await asyncio.sleep(2.5)
        res = await orig_collect(*args, **kwargs)
        res["active_sessions"] = 5
        return res

    monkeypatch.setattr(provider, "_collect_fn", _slow_refresh)
    t0 = time.perf_counter()
    stale_resp = await status_router.get_status()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert 1400.0 <= elapsed_ms < 1850.0, f"Expected SWR soft deadline ~1500ms, got {elapsed_ms:.1f}ms"
    assert stale_resp["active_sessions"] == 2

    # Wait for the background refresh to finish and confirm the cache now serves the updated value (5).
    await asyncio.sleep(1.2)
    fresh_resp = await status_router.get_status()
    assert fresh_resp["active_sessions"] == 5

    # Age past MAX_STALE_S (35.0s > 30.0s): stale snapshot must be evicted and never served.
    provider.age_entries_for_test(35.0)

    async def _fast_new_collect(*args, **kwargs):
        res = await orig_collect(*args, **kwargs)
        res["active_sessions"] = 7
        return res

    monkeypatch.setattr(provider, "_collect_fn", _fast_new_collect)
    after_max_stale = await status_router.get_status()
    assert after_max_stale["active_sessions"] == 7


@pytest.mark.asyncio
async def test_invalidation_during_refresh_discards_older_generation(tmp_path, monkeypatch):
    _configure_isolated_home(monkeypatch, tmp_path)
    provider = status_router.STATUS_SNAPSHOT_PROVIDER
    orig_collect = provider._collect_fn

    await status_router.get_status()
    provider.age_entries_for_test(4.0)

    entered = asyncio.Event()
    release = asyncio.Event()
    gen_values = [10, 20]

    async def _gated_collect(*args, **kwargs):
        val = gen_values.pop(0)
        if val == 10:
            entered.set()
            await release.wait()
        res = await orig_collect(*args, **kwargs)
        res["active_sessions"] = val
        return res

    monkeypatch.setattr(provider, "_collect_fn", _gated_collect)
    monkeypatch.setattr(provider, "swr_soft_deadline_s", 0.05)

    # Trigger background refresh (generation g, returns stale value immediately after 50ms).
    await status_router.get_status()
    await entered.wait()

    # Invalidate while generation g is still in flight, then release generation g.
    provider.invalidate(tmp_path)
    release.set()
    await asyncio.sleep(0.05)

    # Next call must observe generation g+1 (active_sessions == 20), never the discarded 10.
    latest = await status_router.get_status()
    assert latest["active_sessions"] == 20


def test_normative_200_session_2000_message_status_latency_benchmark(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    import hermes_cli.web_server as ws

    now = time.time()
    _configure_isolated_home(monkeypatch, tmp_path)
    _seed_state_db(
        tmp_path / "state.db",
        now=now,
        ended_newer_count=194,
        active_older_count=6,
        messages_count=2000,
    )
    status_router._reset_status_snapshot_cache()

    real_connect = sqlite3.connect
    connect_calls: list[str] = []

    def _spy_connect(database, *args, **kwargs):
        connect_calls.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)

    collect_calls: list[int] = []
    orig_collect = status_router.STATUS_SNAPSHOT_PROVIDER._collect_fn

    async def _counted_collect(*args, **kwargs):
        collect_calls.append(1)
        return await orig_collect(*args, **kwargs)

    monkeypatch.setattr(status_router.STATUS_SNAPSHOT_PROVIDER, "_collect_fn", _counted_collect)

    client = TestClient(ws.app)
    # Pre-warm FastAPI app and plugin discovery so cold burst measures /api/status state.db probe.
    client.get("/api/status")
    status_router._reset_status_snapshot_cache()
    connect_calls.clear()
    collect_calls.clear()

    # 1. 8-Thread Cold Singleflight Burst
    cold_latencies: list[float] = []
    cold_bodies: list[dict[str, Any]] = []
    barrier = threading.Barrier(8)

    def _cold_worker():
        barrier.wait(timeout=5.0)
        t0 = time.perf_counter()
        resp = client.get("/api/status")
        cold_latencies.append((time.perf_counter() - t0) * 1000.0)
        assert resp.status_code == 200
        cold_bodies.append(resp.json())

    threads = [threading.Thread(target=_cold_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(collect_calls) == 1
    assert len(connect_calls) == 1
    assert max(cold_latencies) < 500.0, f"Cold burst max_ms={max(cold_latencies):.2f}ms >= 500ms"
    assert all(b["active_sessions"] == 6 for b in cold_bodies)

    # 2. 10 Sequential Warm Polls
    connect_calls.clear()
    warm_latencies: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        resp = client.get("/api/status")
        warm_latencies.append((time.perf_counter() - t0) * 1000.0)
        assert resp.status_code == 200
        assert resp.json()["active_sessions"] == 6

    median_warm_ms = statistics.median(warm_latencies)
    max_warm_ms = max(warm_latencies)
    assert len(connect_calls) == 0
    assert median_warm_ms < 25.0, f"Warm median_ms={median_warm_ms:.2f}ms >= 25.0ms"
    assert max_warm_ms < 100.0, f"Warm max_ms={max_warm_ms:.2f}ms >= 100.0ms"
