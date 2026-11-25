"""State-machine unit tests for GET /api/profiles singleflight SWR cache (B2.1)."""

from __future__ import annotations

import asyncio
import threading
from functools import wraps

import httpx
import pytest
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from hermes_cli import profiles as profiles_mod
from hermes_cli.web_routers import profiles


def async_test(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return run


@pytest.fixture(autouse=True)
def _isolated_home_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    if hasattr(profiles, "invalidate_profiles_cache"):
        profiles.invalidate_profiles_cache()
    yield
    if hasattr(profiles, "invalidate_profiles_cache"):
        profiles.invalidate_profiles_cache()


@async_test
async def test_fresh_hit_returns_deep_copied_snapshot_without_rescanning(monkeypatch):
    clock = [1000.0]
    calls = []

    def fake_list_profiles(**_kwargs):
        calls.append(1)
        return [f"prof-{len(calls)}"]

    monkeypatch.setattr(profiles_mod, "list_profiles", fake_list_profiles)
    monkeypatch.setattr(profiles, "_profile_to_dict", lambda p: {"name": p, "ui_meta": {"k": 1}})
    monkeypatch.setattr(profiles, "_profiles_monotonic", lambda: clock[0])

    first = await profiles.list_profiles_endpoint()
    assert first == {"profiles": [{"name": "prof-1", "ui_meta": {"k": 1}}]}
    assert len(calls) == 1

    # Mutating caller's copy must not mutate cached snapshot
    first["profiles"][0]["name"] = "mutated"
    first["profiles"][0]["ui_meta"]["k"] = 999

    clock[0] += 5.0  # < 15.0s TTL
    second = await profiles.list_profiles_endpoint()
    assert second == {"profiles": [{"name": "prof-1", "ui_meta": {"k": 1}}]}
    assert len(calls) == 1


@async_test
async def test_stale_hit_serves_immediately_and_triggers_singleflight_background_refresh(monkeypatch):
    clock = [2000.0]
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def fake_list_profiles(**_kwargs):
        calls.append(1)
        idx = len(calls)
        if idx >= 2:
            entered.set()
            assert release.wait(3.0)
        return [f"v{idx}"]

    monkeypatch.setattr(profiles_mod, "list_profiles", fake_list_profiles)
    monkeypatch.setattr(profiles, "_profile_to_dict", lambda p: {"name": p})
    monkeypatch.setattr(profiles, "_profiles_monotonic", lambda: clock[0])
    monkeypatch.setattr(profiles, "run_in_threadpool", run_in_threadpool)

    # Seed initial cache entry at t=2000.0
    initial = await profiles.list_profiles_endpoint()
    assert initial == {"profiles": [{"name": "v1"}]}
    assert len(calls) == 1

    # Advance into stale window (15.0 <= age < 60.0)
    clock[0] = 2020.0
    stale_1 = await profiles.list_profiles_endpoint()
    assert stale_1 == {"profiles": [{"name": "v1"}]}

    await asyncio.to_thread(entered.wait, 2.0)
    assert entered.is_set()

    # Additional requests while refresh is running must NOT spawn a second worker
    stale_2 = await profiles.list_profiles_endpoint()
    stale_3 = await profiles.list_profiles_endpoint()
    assert stale_2 == {"profiles": [{"name": "v1"}]}
    assert stale_3 == {"profiles": [{"name": "v1"}]}
    assert len(calls) == 2

    release.set()
    await profiles._PROFILES_SWR_CACHE.wait_background_idle(timeout=2.0)

    refreshed = await profiles.list_profiles_endpoint()
    assert refreshed == {"profiles": [{"name": "v2"}]}
    assert len(calls) == 2


@async_test
async def test_background_refresh_failure_retains_stale_without_resetting_timestamp_until_max_stale(monkeypatch):
    clock = [3000.0]
    calls = []

    def fake_list_profiles(**_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return ["good-v1"]
        raise RuntimeError("transient disk error")

    monkeypatch.setattr(profiles_mod, "list_profiles", fake_list_profiles)
    monkeypatch.setattr(profiles, "_profile_to_dict", lambda p: {"name": p})
    monkeypatch.setattr(profiles, "_fallback_profile_dicts", lambda _mod: [{"name": "fallback-dir"}])
    monkeypatch.setattr(profiles, "_profiles_monotonic", lambda: clock[0])
    monkeypatch.setattr(profiles, "run_in_threadpool", run_in_threadpool)

    assert await profiles.list_profiles_endpoint() == {"profiles": [{"name": "good-v1"}]}
    assert len(calls) == 1

    # At t=3020 (age=20s), stale hit triggers background refresh which fails
    clock[0] = 3020.0
    assert await profiles.list_profiles_endpoint() == {"profiles": [{"name": "good-v1"}]}
    await profiles._PROFILES_SWR_CACHE.wait_background_idle(timeout=2.0)
    assert len(calls) == 2

    # At t=3055 (age=55s < 60s from original t=3000), stale value is still servable
    clock[0] = 3055.0
    assert await profiles.list_profiles_endpoint() == {"profiles": [{"name": "good-v1"}]}
    await profiles._PROFILES_SWR_CACHE.wait_background_idle(timeout=2.0)

    # At t=3061 (age=61s >= 60s from original t=3000 — proves ts was NOT reset at 3020!),
    # entry is evicted -> cold miss -> fails -> returns fallback without caching fallback
    clock[0] = 3061.0
    expired_resp = await profiles.list_profiles_endpoint()
    assert expired_resp == {"profiles": [{"name": "fallback-dir"}]}


@async_test
async def test_cold_miss_timeout_or_error_never_poisons_cache(monkeypatch):
    should_fail = [True]

    def flaky_list_profiles(**_kwargs):
        if should_fail[0]:
            raise OSError("disk read failed")
        return ["recovered"]

    monkeypatch.setattr(profiles_mod, "list_profiles", flaky_list_profiles)
    monkeypatch.setattr(profiles, "_profile_to_dict", lambda p: {"name": p})
    monkeypatch.setattr(profiles, "_fallback_profile_dicts", lambda _mod: [{"name": "degraded-fallback"}])
    monkeypatch.setattr(profiles, "run_in_threadpool", run_in_threadpool)

    first = await profiles.list_profiles_endpoint()
    assert first == {"profiles": [{"name": "degraded-fallback"}]}

    # Immediately recovers on next request within TTL because fallback was NOT cached
    should_fail[0] = False
    second = await profiles.list_profiles_endpoint()
    assert second == {"profiles": [{"name": "recovered"}]}


@async_test
async def test_mutation_invalidation_discards_earlier_generation_refresh_and_wires_routes(tmp_path, monkeypatch):
    clock = [4000.0]
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def fake_list_profiles(**_kwargs):
        calls.append(1)
        idx = len(calls)
        if idx == 2:
            entered.set()
            assert release.wait(3.0)
            return ["stale-gen-v2"]
        return [f"live-v{idx}"]

    monkeypatch.setattr(profiles_mod, "list_profiles", fake_list_profiles)
    monkeypatch.setattr(profiles, "_profile_to_dict", lambda p: {"name": p})
    monkeypatch.setattr(profiles, "_profiles_monotonic", lambda: clock[0])
    monkeypatch.setattr(profiles, "run_in_threadpool", run_in_threadpool)

    assert await profiles.list_profiles_endpoint() == {"profiles": [{"name": "live-v1"}]}

    # Trigger background refresh from generation 0
    clock[0] = 4020.0
    assert await profiles.list_profiles_endpoint() == {"profiles": [{"name": "live-v1"}]}
    await asyncio.to_thread(entered.wait, 2.0)
    assert entered.is_set()

    # Mutate active profile through HTTP endpoint while gen-0 refresh is mid-flight
    monkeypatch.setattr(profiles_mod, "set_active_profile", lambda name: None)
    app = FastAPI()
    app.include_router(profiles.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        mut = await client.post("/api/profiles/active", json={"name": "default"})
        assert mut.status_code == 200

    # Release the stale-generation worker; it MUST NOT overwrite the invalidated cache
    release.set()
    await profiles._PROFILES_SWR_CACHE.wait_background_idle(timeout=2.0)

    after_mut = await profiles.list_profiles_endpoint()
    assert after_mut == {"profiles": [{"name": "live-v3"}]}
