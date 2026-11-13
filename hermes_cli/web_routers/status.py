"""Status dashboard routes: health, /api/status, system stats, curator, learning graph,
portal and diagnostics actions.

Extracted from ``hermes_cli.web_server``; app state and helpers are late-bound through
:mod:`hermes_cli.web_deps` (cycle-safe, monkeypatch-friendly).
"""

import concurrent.futures
import copy
import importlib
import logging
import re
import asyncio
import os
import sqlite3
import sys
import threading
import time
from contextlib import closing
from fastapi import APIRouter
from hermes_cli.web_deps import LateState, _server, late
from hermes_cli.web_server_gateway import _display_system_platform
from starlette.concurrency import run_in_threadpool
from fastapi import HTTPException, Request
from gateway.status import (
    derive_gateway_busy, derive_gateway_drainable, normalize_updated_at, parse_active_agents,
    profile_platforms_from_multiplexer, resolve_gateway_liveness,
    retained_gateway_state,
    runtime_status_heartbeat_age_s, runtime_status_is_stale)
from gateway.status_served import local_liveness_pid
from hermes_cli import __release_date__
from hermes_cli.config import get_config_path, get_env_path
from hermes_cli.isolated_scope import isolated_launch_profile, scope_to_launch_profile
from hermes_cli.version_info import get_version_info
from hermes_constants import get_process_hermes_home, profile_name_for_home
from hermes_cli.web_models import CuratorPause, LearningNodeRef, LearningNodeEdit, DebugShareRequest
from hermes_cli.web_routers._common import config_scoped_to_thread, destructive_profile, scoped_to_thread
from hermes_cli.web_routers.status_public import redact_status_for_unverified
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple, TypedDict

_log = logging.getLogger("hermes_cli.web_server")
router = APIRouter()
# Mounted separately by web_server so /api/logs keeps its original route-table position.
logs_router = APIRouter()

# Late-bound so a test's monkeypatch on the owning module wins at call time.
_collect_profile_gateway_topology_cached = late("_collect_profile_gateway_topology_cached", "hermes_cli.web_server_gateway")
_config_profile_scope = late("_config_profile_scope", "hermes_cli.web_server_profiles")
_dashboard_local_update_managed_externally = late("_dashboard_local_update_managed_externally", "hermes_cli.web_server_files")
_load_configured_gateway_platforms = late("_load_configured_gateway_platforms", "hermes_cli.web_server_gateway")
_probe_gateway_health = late("_probe_gateway_health", "hermes_cli.web_server_gateway")
_require_token = late("_require_token")
_resolve_profile_dir = late("_resolve_profile_dir", "hermes_cli.web_server_profiles")
_resolve_restart_drain_timeout = late("_resolve_restart_drain_timeout", "hermes_cli.web_server_lifecycle")
_spawn_hermes_action = late("_spawn_hermes_action", "hermes_cli.web_server_gateway")
_ssh_runtime_intact = late("_ssh_runtime_intact")
app = LateState("app")  # the FastAPI instance (app.state.*)
check_config_version = late("check_config_version", "hermes_cli.config")
get_hermes_home = late("get_hermes_home", "hermes_cli.config")
_profile_cli_args = late("_profile_cli_args", "hermes_cli.web_server_profiles")
get_install_id = late("get_install_id")
get_running_pid_cached = late("get_running_pid_cached", "gateway.status")
get_runtime_status_running_pid = late("get_runtime_status_running_pid", "gateway.status")
served_profile_runtime = late("served_profile_runtime", "gateway.status_served")
load_config = late("load_config", "hermes_cli.config")
read_runtime_status = late("read_runtime_status", "gateway.status")
_open_session_db_for_profile = late("_open_session_db_for_profile", "hermes_cli.web_server_sessions")


_STATUS_ACTIVE_SESSIONS_TIMEOUT = 0.75
_GATEWAY_HEALTH_ROUTE_TIMEOUT = 1.0
_HEALTHY_PLATFORM_STATES = {"connected", "running", "ok"}
_STATUS_ACTIVE_SESSION_WINDOW_S = 300.0


class StateDbStatusResult(TypedDict):
    active_sessions: int
    fts_rebuild: Optional[dict[str, Any]]
    storage_component: dict[str, str]


def _probe_sqlite_master(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        return {"status": "ok"}
    except sqlite3.DatabaseError:
        return {"status": "degraded", "reason": "corrupt"}
    except Exception:
        return {"status": "degraded"}


def probe_state_db_status(
    db_path: Path,
    *,
    active_window_s: float = _STATUS_ACTIVE_SESSION_WINDOW_S,
    now: float | None = None,
) -> StateDbStatusResult:
    """Single-pass read-only inspection of state.db for /api/status.

    Opens at most one SQLite connection (mode=ro, query_only=1) and extracts:
    1. storage readiness (sqlite_master probe matching gateway.readiness._probe_state_db),
    2. active session count via idx_sessions_effective_activity without scanning messages,
    3. deferred FTS rebuild progress from state_meta.

    Never creates state.db or sidecars if missing. Never runs expensive PRAGMA quick_check.
    """
    path = Path(db_path)
    if not path.is_file():
        return {
            "active_sessions": 0,
            "fts_rebuild": None,
            "storage_component": {"status": "ok"},
        }

    from hermes_state_health import STORAGE_CORRUPT, note_storage_error, storage_state

    if storage_state(path) == STORAGE_CORRUPT:
        return {
            "active_sessions": 0,
            "fts_rebuild": None,
            "storage_component": {"status": "degraded", "reason": "corrupt"},
        }

    wall_now = time.time() if now is None else float(now)
    cutoff = wall_now - max(0.0, float(active_window_s))

    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1.5)) as conn:
            conn.execute("PRAGMA query_only = ON")

            # 1. Storage readiness (matches gateway.readiness._probe_state_db: schema read only)
            storage_comp = _probe_sqlite_master(conn)
            if storage_comp.get("reason") == "corrupt":
                note_storage_error(path, sqlite3.DatabaseError("corrupt state.db"))
                return {
                    "active_sessions": 0,
                    "fts_rebuild": None,
                    "storage_component": storage_comp,
                }

            # 2. Active sessions
            active_count = 0
            try:
                table_info = conn.execute("PRAGMA table_info(sessions)").fetchall()
                cols = {r[1] for r in table_info} if table_info else set()
                if "id" in cols:
                    where = ["ended_at IS NULL"]
                    if "parent_session_id" in cols:
                        where.append("parent_session_id IS NULL")
                    if "hidden" in cols:
                        where.append("COALESCE(hidden, 0) = 0")
                    if "last_activity_at" in cols and "started_at" in cols:
                        where.append("COALESCE(last_activity_at, started_at) >= ?")
                    elif "last_activity_at" in cols:
                        where.append("last_activity_at >= ?")
                    elif "started_at" in cols:
                        where.append("started_at >= ?")
                    else:
                        where.append("1 = ?")

                    sql = f"SELECT COUNT(*) FROM sessions WHERE {' AND '.join(where)}"
                    row = conn.execute(sql, (cutoff,)).fetchone()
                    active_count = int(row[0]) if row else 0
            except Exception as exc:
                _log.debug("probe_state_db_status sessions query failed: %s", exc)
                active_count = 0

            # 3. FTS rebuild progress
            fts_rebuild = None
            try:
                meta_rows = conn.execute(
                    "SELECT key, value FROM state_meta WHERE key IN ('fts_rebuild_high_water', 'fts_rebuild_progress')"
                ).fetchall()
                meta = {r[0]: r[1] for r in meta_rows}
                high_water = meta.get("fts_rebuild_high_water")
                if high_water is not None and int(high_water) > 0:
                    total = int(high_water)
                    progress = int(meta.get("fts_rebuild_progress") or 0)
                    fts_rebuild = {
                        "pending": True,
                        "total": total,
                        "indexed": progress,
                        "percent": min(100, int(100 * progress / total)),
                    }
            except Exception as exc:
                _log.debug("probe_state_db_status fts_rebuild query failed: %s", exc)
                fts_rebuild = None

            return {
                "active_sessions": active_count,
                "fts_rebuild": fts_rebuild,
                "storage_component": storage_comp,
            }
    except Exception as exc:
        if note_storage_error(path, exc):
            return {
                "active_sessions": 0,
                "fts_rebuild": None,
                "storage_component": {"status": "degraded", "reason": "corrupt"},
            }
        return {
            "active_sessions": 0,
            "fts_rebuild": None,
            "storage_component": {"status": "degraded"},
        }


def _count_status_active_sessions() -> int:
    """Best-effort status garnish. Opens read-only (via the shared stale-schema heal) so
    /api/status never routinely writes to state.db while another Hermes process uses it."""
    from hermes_state import _default_db_path
    if not Path(_default_db_path()).exists():
        return 0
    db = _open_session_db_for_profile(None, read_only=True)
    try:
        sessions = db.list_sessions_rich(limit=50, compact_rows=True)
        now = time.time()
        return sum(1 for s in sessions if s.get("ended_at") is None
                   and (now - s.get("last_active", s.get("started_at", 0))) < 300)
    finally:
        db.close()


def _fts_rebuild_status() -> Optional[dict[str, Any]]:
    """Deferred FTS rebuild progress of this home's state.db (read-only open), or None."""
    from hermes_state import SessionDB as _SDB
    from hermes_constants import get_hermes_home as _ghh
    _db_path = _ghh() / "state.db"
    if not _db_path.exists():
        return None
    _sdb = _SDB(db_path=_db_path, read_only=True)
    try:
        return _sdb.fts_rebuild_status()
    finally:
        _sdb.close()


_DEFAULT_COUNT_FN = _count_status_active_sessions
_DEFAULT_FTS_FN = _fts_rebuild_status
try:
    from gateway.readiness import _probe_state_db as _DEFAULT_READINESS_PROBE_FN
except Exception:
    _DEFAULT_READINESS_PROBE_FN = None

try:
    from gateway.status import read_runtime_status as _DEFAULT_READ_RUNTIME_STATUS_FN
except Exception:
    _DEFAULT_READ_RUNTIME_STATUS_FN = None

try:
    from gateway.status_served import local_liveness_pid as _DEFAULT_LOCAL_LIVENESS_PID_FN
except Exception:
    _DEFAULT_LOCAL_LIVENESS_PID_FN = None


def _safe_call(mod, fn_name: str, default):
    try:
        fn = getattr(mod, fn_name, None)
        return fn() if callable(fn) else default
    except Exception:
        return default


async def _status_active_sessions() -> int:
    try:
        from hermes_cli.web_server import offload_probe
        return await asyncio.wait_for(
            offload_probe(_count_status_active_sessions),
            timeout=_STATUS_ACTIVE_SESSIONS_TIMEOUT)
    except TimeoutError:
        _log.debug("/api/status active session count exceeded %.2fs; returning 0",
                   _STATUS_ACTIVE_SESSIONS_TIMEOUT)
    except Exception as exc:
        _log.debug("/api/status active session count unavailable: %s", exc)
    return 0


def _is_legacy_db_probe_monkeypatched() -> bool:
    try:
        from gateway.readiness import _probe_state_db as curr_readiness
    except Exception:
        curr_readiness = None
    return (
        _count_status_active_sessions is not _DEFAULT_COUNT_FN
        or _fts_rebuild_status is not _DEFAULT_FTS_FN
        or curr_readiness is not _DEFAULT_READINESS_PROBE_FN
    )


def _is_status_runtime_probe_monkeypatched() -> bool:
    try:
        from gateway.status import read_runtime_status as curr_read_status
    except Exception:
        curr_read_status = None
    try:
        from gateway.status_served import local_liveness_pid as curr_liveness
    except Exception:
        curr_liveness = None
    return (
        curr_read_status is not _DEFAULT_READ_RUNTIME_STATUS_FN
        or curr_liveness is not _DEFAULT_LOCAL_LIVENESS_PID_FN
    )


_CONFIG_DERIVED_CACHE: dict[str, Any] = {
    "local_reads_sig": None,
    "local_reads": None,
    "restart_drain_sig": None,
    "restart_drain": None,
}
_CONFIG_DERIVED_LOCK = threading.Lock()


def _stat_file_sig(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (-1, -1)


def _reset_config_derived_cache() -> None:
    with _CONFIG_DERIVED_LOCK:
        _CONFIG_DERIVED_CACHE["local_reads_sig"] = None
        _CONFIG_DERIVED_CACHE["local_reads"] = None
        _CONFIG_DERIVED_CACHE["restart_drain_sig"] = None
        _CONFIG_DERIVED_CACHE["restart_drain"] = None


def _status_local_reads() -> dict[str, Any]:
    """The probe's synchronous local reads: the raw config.yaml stamp, the install method, the
    auth store and (first call only) git provenance. Cached by config.yaml stat signature."""
    from hermes_cli.config import check_config_version
    cfg_path = Path(get_config_path())
    sig = (str(cfg_path), *_stat_file_sig(cfg_path), id(check_config_version))
    with _CONFIG_DERIVED_LOCK:
        cached = _CONFIG_DERIVED_CACHE.get("local_reads")
        if _CONFIG_DERIVED_CACHE.get("local_reads_sig") == sig and cached is not None:
            res = dict(cached)
            res["nous_session_valid"] = _nous_session_validity()
            return res

    current_ver, latest_ver = check_config_version()
    reads = {
        "version": get_version_info().base_version,
        "config_version": current_ver,
        "latest_config_version": latest_ver,
        "can_update_hermes": not _dashboard_local_update_managed_externally(),
        "nous_session_valid": _nous_session_validity(),
    }
    with _CONFIG_DERIVED_LOCK:
        _CONFIG_DERIVED_CACHE["local_reads_sig"] = sig
        _CONFIG_DERIVED_CACHE["local_reads"] = dict(reads)
    return reads


def _resolve_restart_drain_timeout_cached() -> float:
    drain_fn = _resolve_restart_drain_timeout
    cfg_path = Path(get_config_path())
    sig = (str(cfg_path), *_stat_file_sig(cfg_path), id(drain_fn))
    with _CONFIG_DERIVED_LOCK:
        cached = _CONFIG_DERIVED_CACHE.get("restart_drain")
        if _CONFIG_DERIVED_CACHE.get("restart_drain_sig") == sig and cached is not None:
            return float(cached)

    val = float(drain_fn())
    with _CONFIG_DERIVED_LOCK:
        _CONFIG_DERIVED_CACHE["restart_drain_sig"] = sig
        _CONFIG_DERIVED_CACHE["restart_drain"] = val
    return val


@router.get("/api/ssh/ownership")
async def get_ssh_ownership(request: Request):
    from hermes_cli.web_server import _SSH_OWNER_NONCE
    _require_token(request)
    if not _SSH_OWNER_NONCE:
        raise HTTPException(status_code=404, detail="SSH ownership is not active")
    return {"ok": True, "sshOwnerNonce": _SSH_OWNER_NONCE, "protocolVersion": 1,
            "runtimeIntact": _ssh_runtime_intact()}


@router.get("/api/health")
async def get_health():
    """Lightweight process liveness for desktop/backend readiness probes.

    ``commit`` is the code this process BOOTED from (``get_version_info`` is cached at
    ``web_server`` import): Desktop refuses to attach to a backend whose commit differs from
    its checkout, so a serve that outlived ``hermes update`` is never re-adopted.
    """
    info = get_version_info()
    return {"ok": True, "version": info.base_version, "displayVersion": info.display_version,
            "commit": info.commit,
            "auth_required": bool(getattr(app.state, "auth_required", False))}


@router.get("/api/host/identity")
async def get_host_identity(request: Request):
    """Prove to an attaching `hermes serve`/`dashboard` WHO owns this port.

    The host rendezvous record names a (pid, port) owner, but a record cannot say whether that
    owner still holds the port: a graceful-shutdown window or an unrelated listener that
    inherited the port both look identical on disk. The attaching side dials this endpoint with
    the owner's 0600 token and attaches only when pid+role match. ``servesSpa`` is false for
    headless ``serve``, so a `hermes dashboard` user is never routed to a backend with no UI.
    """
    _require_token(request)
    # ``role`` is the host ROLE this process published (gateway/host_rendezvous.ROLE_SERVE, or
    # ROLE_DESKTOP_SERVE for a Desktop-owned child), not the launch mode: `hermes serve` and
    # `hermes dashboard` are one host role that differ in SPA.
    return {"ok": True, "protocolVersion": 1, "pid": os.getpid(),
            "role": getattr(app.state, "host_role", None) or "serve",
            "servesSpa": bool(getattr(app.state, "serves_spa", False))}


# The idle proof and retirement fence cover a standalone ``hermes serve`` child's ledgers only. On
# the gateway's listener the agent work lives on the runner, which retires through its own idle exit
# (``gateway/run_idle_exit.py``); there this route never proves idle nor takes the fence.
_GATEWAY_OWNS_LIFECYCLE = "gateway_owns_lifecycle"


def _mounted_on_gateway(request: Request) -> bool:
    return getattr(request.app.state, "gateway_runner", None) is not None


@router.get("/api/health/idle")
async def get_health_idle(request: Request):
    """Token-gated diagnostic snapshot; never a retirement permit. None means cannot prove idle."""
    from hermes_cli.web_server_idle_proof import idle_proof
    _require_token(request)
    if _mounted_on_gateway(request):
        return {"ok": True, "idle": False, "reason": _GATEWAY_OWNS_LIFECYCLE}
    return {"ok": True, **idle_proof()}


@router.post("/api/health/retirement")
async def post_health_retirement(request: Request):
    from hermes_cli.backend_retirement import retirement

    _require_token(request)
    if _mounted_on_gateway(request):
        return {"ok": False, "idle": False, "reason": _GATEWAY_OWNS_LIFECYCLE}
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON body required")
    if not isinstance(body, dict) or body.get("action") not in ("prepare", "commit", "cancel"):
        raise HTTPException(status_code=400, detail="action must be prepare, commit, or cancel")
    action = body["action"]
    if action == "prepare":
        return await run_in_threadpool(retirement.prepare)
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=400, detail="token required")
    return getattr(retirement, action)(token)


# Profile segment mirrors hermes_cli.profiles._PROFILE_ID_RE. Platform segment mirrors the
# Platform enum's normalized values: built-in members plus plugin directory names
# (lowercased), which allow hyphens as well as underscores (e.g. ``reviewer:foo-bar``).
_PROFILE_PLATFORM_STATUS_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}:[a-z0-9][a-z0-9_-]{0,63}$")


def _is_profile_platform_status_key(key: object) -> bool:
    """Accept only the runner's public ``<profile>:<platform>`` key grammar."""
    return isinstance(key, str) and bool(_PROFILE_PLATFORM_STATUS_KEY_RE.fullmatch(key))


def _status_platform_key_allowed(key: object, configured: set[str] | None) -> bool:
    """Whether a runtime-status platform key may appear publicly: namespaced
    ``<profile>:<platform>`` keys are validated against the grammar *unconditionally* (a
    failed config-set load must not fail open into projecting arbitrary keys from a
    process-local JSON file); plain keys are checked against the configured set when it
    loaded, passed through when it did not."""
    if not isinstance(key, str):
        return False
    if ":" in key:
        return _is_profile_platform_status_key(key)
    return configured is None or key in configured


# Per-entry writer-identity stamps (added by gateway.status.write_runtime_status for the
# aggregation ownership check) are process recon — the same class of detail as the
# auth-gated top-level ``gateway_pid`` — and must not project onto the public endpoint.
_PRIVATE_PLATFORM_ENTRY_KEYS = frozenset({"writer_pid", "writer_start_time"})


def _public_platform_entry(value: Any) -> Any:
    """Strip writer-identity stamps from a platform entry before projection."""
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if k not in _PRIVATE_PLATFORM_ENTRY_KEYS}


def _merge_profile_gateway_platforms(gateway_platforms: dict, profile_platforms: dict) -> dict:
    """Merge independent per-profile gateway platform states: hosts running separate gateway
    services per profile (``gateway_mode == "multiple"``) persist each profile's platform
    failures in its own ``gateway_state.json``, invisible to the machine-level probe NAS
    reads unless folded in under the validated ``<profile>:<platform>`` grammar. The active
    profile's own map is skipped (already present); existing keys are never overwritten."""
    try:
        from hermes_cli.profiles import get_active_profile_name
        active = get_active_profile_name()
    except Exception:
        active = "default"
    merged = dict(gateway_platforms)
    for prof, plats in (profile_platforms or {}).items():
        if prof == active or not isinstance(plats, dict):
            continue
        for key, value in plats.items():
            if not isinstance(key, str) or ":" in key or not isinstance(value, dict):
                continue
            namespaced = f"{prof}:{key}"
            if not _is_profile_platform_status_key(namespaced):
                continue
            merged.setdefault(namespaced, _public_platform_entry(value))
    return merged


# --- Gateway liveness detection --- Delegated to the single shared ladder in gateway.status so this
# endpoint and /api/messaging/platforms can never disagree about whether the gateway is up (they used to:
# sidebar "running" while the Channels page rendered "The gateway is not running"). When ?profile=<name> was
# given, scope PID and state reads to that profile's directory — gateway identity files (PID, lock, runtime
# status) are written to the per-profile home, not the process-level HERMES_HOME (see issue #69143). Plain
# /api/status keeps the exact zero-arg call so its behavior (and cache signature) is unchanged. The
# module-level probe references are handed to the resolver so the long-standing
# `monkeypatch.setattr(gateway.status, "get_running_pid_cached", ...)` seam used across the test-suite still
# intercepts them.
def _bounded_health_probe():
    """Health probe with the route's blocking-call budget preserved. The resolver only
    reaches this rung when the local PID probe came up empty, so the timeout is paid at
    most once per request and only in the cross-container case that needs it."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_probe_gateway_health)
        try:
            return future.result(timeout=_GATEWAY_HEALTH_ROUTE_TIMEOUT)
        except concurrent.futures.TimeoutError:
            _log.warning("/api/status gateway health probe exceeded %.2fs; using local status",
                         _GATEWAY_HEALTH_ROUTE_TIMEOUT)
            return False, None
        except Exception:
            return False, None


def _project_gateway_platforms(gateway_platforms: dict, configured: set[str] | None,
                               gateway_running: bool, gateway_state) -> dict:
    """Public projection of a runtime's platform map (see ``_status_platform_key_allowed``
    for the key rules). A cleanly stopped gateway's platform states are stale noise and are
    cleared so a dead process can't report "connected"; a startup_failed gateway's FATAL
    entries are the diagnosis (credential collisions, auth failures) that the single
    exit_reason string can't express, so they are kept — upstream writer-identity/freshness
    filtering already dropped other processes' entries."""
    platforms = {key: _public_platform_entry(value) for key, value in gateway_platforms.items()
                 if _status_platform_key_allowed(key, configured)}
    if gateway_running:
        return platforms
    if gateway_state == "startup_failed":
        return {key: value for key, value in platforms.items()
                if isinstance(value, dict) and value.get("state") == "fatal"}
    return {}


async def _resolve_gateway_status(profile_dir: Optional[Path], health_url) -> dict[str, Any]:
    """Liveness + runtime-state readout (running/pid/state/platforms/exit_reason/updated_at
    plus the raw ``runtime`` document).

    Liveness is delegated to the shared ladder in gateway.status so this endpoint and
    /api/messaging/platforms can never disagree. With ``?profile=<name>`` PID/state reads are
    scoped to that profile's directory (gateway identity files live in the per-profile
    home); plain /api/status keeps the exact zero-arg call so its cache signature is
    unchanged. The module-level probe references are handed to the resolver so the
    ``monkeypatch.setattr(gateway.status, "get_running_pid_cached", ...)`` seam still intercepts.
    """
    local_runtime = (read_runtime_status(path=profile_dir / "gateway_state.json")
                     if profile_dir else read_runtime_status())
    liveness = await run_in_threadpool(lambda: resolve_gateway_liveness(
        profile_dir=profile_dir, runtime=local_runtime,
        health_probe=_bounded_health_probe if health_url else None,
        pid_probe=get_running_pid_cached, runtime_reader=read_runtime_status,
        runtime_pid_probe=get_runtime_status_running_pid))
    gateway_running = liveness.running
    remote_health_body: dict | None = liveness.health_body

    try:
        configured = await run_in_threadpool(_load_configured_gateway_platforms)
    except Exception:
        configured = None

    # Prefer the detailed health endpoint response (has full state) when the local runtime
    # status file is absent or stale (cross-container).
    runtime = local_runtime
    if runtime is None and remote_health_body and remote_health_body.get("gateway_state"):
        runtime = remote_health_body
    # Unscoped, the profile is the process's own home (a pooled ``hermes --profile X serve``).
    own_home = profile_dir or get_process_hermes_home()
    if liveness.runtime is not None:
        # Served by the multiplexer: its record is this profile's runtime, with the profile's own
        # adapters under ``<profile>:<platform>`` re-keyed to the standalone shape.
        # Fold on the profile NAME, never ``profile_dir.name``: ``?profile=default`` resolves the
        # root itself, whose basename (``.hermes``) matched nothing and read as a named id (#123088).
        served_name = profile_name_for_home(own_home)
        runtime = {**liveness.runtime,
                   "platforms": profile_platforms_from_multiplexer(liveness.runtime, served_name or "")}
    elif local_liveness_pid(liveness) is not None:
        # The PID rung answers before the multiplexer rung, and a served profile's gateway.lock is
        # held by the multiplexer itself, so the ladder returns without its record while the
        # profile's own gateway_state.json may be a retired standalone gateway's (#139080). The
        # shared rule (also the messaging card's) picks the multiplexer record only when the lock
        # holder IS the multiplexer and the profile's own record proves no live gateway.
        try:
            runtime = await run_in_threadpool(
                served_profile_runtime, own_home, local_runtime, gateway_pid=local_liveness_pid(liveness)) or runtime
        except Exception:  # health: allow BLE001 -- status route must never 500; traceback is logged
            # A failed lookup keeps the profile's own record.
            _log.warning("/api/status multiplexer record lookup failed", exc_info=True)

    gateway_state = None
    gateway_platforms: dict = {}
    gateway_exit_reason = None
    gateway_updated_at = None
    gateway_heartbeat_stale_s = None
    if runtime:
        gateway_state = runtime.get("gateway_state")
        if not gateway_running:
            # Shared with /api/messaging/platforms: a durable operator stop outranks a retained
            # ``startup_failed`` / watchdog ``degraded`` (kept on disk for diagnostics), so the
            # overview does not alarm on it.
            gateway_state = retained_gateway_state(runtime)
        elif remote_health_body is not None and gateway_state in {None, "stopped"}:
            # The health probe confirmed the gateway is alive, but the local runtime status
            # file may be stale (cross-container): override so the badge is correct.
            gateway_state = "running"
        elif gateway_state in {"running", "degraded", "starting"} and runtime_status_is_stale(runtime):
            # Alive PID, but housekeeping stopped re-stamping the heartbeat: the loop or the
            # housekeeping thread wedged while the file still says 'running' (#113372). Same arm
            # as ``hermes gateway status`` so the sidebar strip and the CLI agree.
            gateway_heartbeat_stale_s = runtime_status_heartbeat_age_s(runtime)
        gateway_platforms = _project_gateway_platforms(
            runtime.get("platforms") or {}, configured, gateway_running, gateway_state)
        gateway_exit_reason = None if gateway_state == "stopped" else runtime.get("exit_reason")
        # Contract: gateway_updated_at is RFC3339 string | null, never a number. ``runtime``
        # may be the local gateway_state.json (legacy gateways wrote epoch floats; hand
        # edits can inject anything) or a remote /health/detailed body — normalize both.
        gateway_updated_at = normalize_updated_at(runtime.get("updated_at"))

    # No runtime info at all but the health probe confirmed alive (no shared volume).
    if gateway_running and gateway_state is None and remote_health_body is not None:
        gateway_state = "running"

    # A live multiplexer's record (the host's own, or the one serving this profile) names every
    # profile a restart would blip ("default, alpha, beta"); a standalone gateway's names none.
    shared_with = (runtime or {}).get("served_profiles") if gateway_running else None
    return {
        "runtime": runtime, "gateway_running": gateway_running, "gateway_pid": liveness.pid,
        "gateway_state": gateway_state, "gateway_platforms": gateway_platforms,
        "gateway_exit_reason": gateway_exit_reason, "gateway_updated_at": gateway_updated_at,
        "gateway_heartbeat_stale_s": gateway_heartbeat_stale_s,
        "gateway_shared_with": [str(p) for p in shared_with] if isinstance(shared_with, list) else None}


def _auth_gate_status() -> dict[str, Any]:
    """Dashboard auth gate readout: gate engaged, registered providers, and the RFC 8252
    native-app capability advertisement ``auth_flows`` the desktop reads to pick the
    system-browser + loopback + PKCE flow over the embedded-webview cookie flow. "cookie" is
    always available in gated mode; "native_pkce" when at least one interactive session
    provider is registered (token-only credentials such as drain, and provisioned-credential
    session providers such as session-token, don't count). Missing
    "native_pkce" ⇒ older gateway ⇒ desktop falls back automatically. ``peer_access`` is the
    peer-pass contract this agent takes on ``/api/peer`` (0: none), which the pass issuer reads."""
    auth_required = bool(getattr(app.state, "auth_required", False))
    auth_providers: list[str] = []
    auth_flows: list[str] = []
    peer_access = 0
    try:
        from hermes_cli.dashboard_auth import (
            list_interactive_providers as _list_interactive_providers,
            list_providers as _list_providers)
        from hermes_cli.dashboard_auth.peer_pass import peer_access_contract
        auth_providers = [p.name for p in _list_providers()]
        peer_access = peer_access_contract()
        if auth_required:
            auth_flows.append("cookie")
            if _list_interactive_providers():
                auth_flows.append("native_pkce")
    except Exception:
        # Module not importable yet (early startup) — leave as [].
        pass
    return {"auth_required": auth_required, "auth_providers": auth_providers,
            "auth_flows": auth_flows, "peer_access": peer_access}


def _nous_session_validity() -> str:
    """Nous bootstrap-session validity for the NAS health sweep: a hosted agent whose Nous
    auth dies terminally looks HEALTHY to every liveness probe yet every inference turn
    fails, and this is the ONLY signal that surfaces it (local auth-store state, no token
    needed). Best-effort: never let auth classification break the probe."""
    try:
        from hermes_cli.auth import get_nous_session_validity
        return get_nous_session_validity()
    except Exception:
        return "unknown"


async def _component_health(
    gateway: dict[str, Any],
    *,
    storage_component: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Component-level health rollup: counts and status enums only (public payload — no
    messages, paths or other detail that could carry secrets). The storage probe reuses the
    gateway readiness state_db check (read-only, 1s-bounded) off-loop."""
    from hermes_cli.web_server import DASHBOARD_HEALTH
    gateway_running, gateway_state = gateway["gateway_running"], gateway["gateway_state"]
    gateway_platforms = gateway["gateway_platforms"]
    components: dict[str, Any] = {
        "gateway": {
            "status": "ok" if gateway_running and gateway_state in {"running", "draining"} else "degraded",
            "state": gateway_state or ("running" if gateway_running else "stopped")},
        "dashboard": DASHBOARD_HEALTH.snapshot()}

    if storage_component is not None and not _is_legacy_db_probe_monkeypatched():
        components["storage"] = dict(storage_component)
    else:
        try:
            from gateway.readiness import _probe_state_db
            storage_check = await run_in_threadpool(_probe_state_db, get_hermes_home())
            components["storage"] = {"status": storage_check.get("status", "degraded")}
            # The one reason enum consumers key off; same latch as readiness and the session lists.
            if storage_check.get("detail") == "corrupt":
                components["storage"]["reason"] = "corrupt"
        except Exception:
            components["storage"] = {"status": "degraded"}
    # ``disabled`` entries are platforms the multiplexer deliberately does not run for a served profile
    # (shared ingress owned by the default) — informational, never a degraded verdict.
    platform_states = [str(value.get("state") or value.get("status") or "").lower()
                       for value in gateway_platforms.values() if isinstance(value, dict)]
    platform_states = [state for state in platform_states if state != "disabled"]
    connected = sum(1 for state in platform_states if state in _HEALTHY_PLATFORM_STATES)
    components["platforms"] = {"status": "ok" if connected == len(platform_states) else "degraded",
                               "configured": len(platform_states), "connected": connected}
    return components


async def _advisory_pressure(
    status: dict[str, Any],
    home: Path,
    *,
    fts_rebuild: Optional[dict[str, Any]] = None,
    fts_precomputed: bool = False,
) -> None:
    """Memory / disk pressure rollups + deferred FTS rebuild progress (coarse numbers/enums
    only; public payload). Deliberately NOT folded into components/overall: pressure is
    advisory, not a liveness verdict, and flipping ``overall`` on it would page NAS's
    availability sweep for a condition the valve is already handling. Read-only, never raise.
    """
    for key, mod_name, fn_name in (("memory", "gateway.memory_status", "collect_memory_status"),
                                   ("disk", "gateway.disk_status", "collect_disk_status")):
        try:
            collect = getattr(importlib.import_module(mod_name), fn_name)
            status[key] = await run_in_threadpool(collect, home)
        except Exception:
            status[key] = {"pressure": "unknown"}

    if fts_precomputed and not _is_legacy_db_probe_monkeypatched():
        if fts_rebuild is not None:
            status["fts_rebuild"] = fts_rebuild
    else:
        try:
            # SQLite open + read: off the loop like the rest of the probe.
            _rebuild = await run_in_threadpool(_fts_rebuild_status)
            if _rebuild is not None:
                status["fts_rebuild"] = _rebuild
        except Exception as exc:
            import logging
            logging.getLogger("hermes.web.status").debug("legacy _fts_rebuild_status error: %s", exc)


#: What the gateway's Webapp answers a caller who has not signed in: liveness, version and the
#: sign-in shape (the native-app login probe and uptime checks read nothing else). Profile names,
#: the install id and host figures wait for sign-in, since a Webapp on a wildcard bind is reachable
#: by everyone on the network. The standalone dashboard keeps its public body: Hermes Cloud reads
#: the profile list there without a session (#60585).
_WEBAPP_SIGNED_OUT_STATUS_KEYS = frozenset({
    "version", "release_date", "gateway_running", "gateway_state", "overall", "components",
    "auth_required", "auth_providers", "auth_flows"})


async def _status_caller_verified(request: Request) -> bool:
    """True when the caller carries a credential the auth gate would accept on a gated route
    (session cookie/bearer, native principal, or the loopback session token). ``/api/status`` is
    public, so the gate never verified one. ``None`` is an in-process caller, not a network one."""
    from hermes_cli.web_server import _require_token
    if request is None:
        return True
    if getattr(request.app.state, "auth_required", False) and getattr(request.state, "session", None) is None:
        from hermes_cli.dashboard_auth.middleware import peek_session
        request.state.session = await run_in_threadpool(peek_session, request)
    try:
        _require_token(request)
    except HTTPException:
        return False
    return True


def _webapp_caller_signed_out(request: Request, verified: bool) -> bool:
    """True when this request reached the gateway's Webapp behind its sign-in gate unverified."""
    from hermes_cli.web_server_surface import surface
    return (not verified and surface(request.app.state) == "webapp"
            and bool(getattr(request.app.state, "auth_required", False)))


async def _collect_canonical_status_snapshot(
    hermes_home: Path,
    requested_profile: str,
    profile_dir: Optional[Path],
    gateway_health_url: str,
) -> dict[str, Any]:
    local = await run_in_threadpool(_status_local_reads)
    gateway = await _resolve_gateway_status(profile_dir, gateway_health_url)
    gateway_running, gateway_state = gateway["gateway_running"], gateway["gateway_state"]

    topology = await run_in_threadpool(_collect_profile_gateway_topology_cached)
    if not requested_profile:
        gateway["gateway_platforms"] = _merge_profile_gateway_platforms(
            gateway["gateway_platforms"], topology.get("profile_platforms") or {})

    db_path = (profile_dir if profile_dir else hermes_home) / "state.db"
    if _is_legacy_db_probe_monkeypatched():
        active_sessions = await _status_active_sessions()
        storage_component = None
        fts_rebuild = None
        fts_precomputed = False
    else:
        from hermes_cli.web_server import offload_probe
        db_res = await offload_probe(probe_state_db_status, db_path)
        active_sessions = db_res["active_sessions"]
        storage_component = db_res["storage_component"]
        fts_rebuild = db_res["fts_rebuild"]
        fts_precomputed = True

    active_agents = parse_active_agents((gateway["runtime"] or {}).get("active_agents", 0))
    restart_drain_timeout = await run_in_threadpool(_resolve_restart_drain_timeout_cached)

    status: dict[str, Any] = {
        "version": local["version"],
        "release_date": __release_date__,
        "config_version": local["config_version"],
        "latest_config_version": local["latest_config_version"],
        "can_update_hermes": local["can_update_hermes"],
        "gateway_running": gateway_running,
        "gateway_state": gateway_state,
        "gateway_platforms": gateway["gateway_platforms"],
        "gateway_exit_reason": gateway["gateway_exit_reason"],
        "gateway_updated_at": gateway["gateway_updated_at"],
        "gateway_heartbeat_stale_s": gateway["gateway_heartbeat_stale_s"],
        "gateway_shared_with": gateway["gateway_shared_with"],
        "active_agents": active_agents,
        "gateway_busy": derive_gateway_busy(
            gateway_running=gateway_running,
            gateway_state=gateway_state,
            active_agents=active_agents,
        ),
        "gateway_drainable": derive_gateway_drainable(
            gateway_running=gateway_running,
            gateway_state=gateway_state,
        ),
        "restart_drain_timeout": restart_drain_timeout,
        "active_sessions": active_sessions,
        "nous_session_valid": local["nous_session_valid"],
    }

    install_id = await run_in_threadpool(get_install_id)
    if install_id:
        status["install_id"] = install_id

    from hermes_cli.shared_profile_warning import shared_profile_warning
    status["shared_profile_warning"] = bool(await run_in_threadpool(shared_profile_warning))

    components = await _component_health(gateway, storage_component=storage_component)
    status["components"] = components
    status["overall"] = "ok" if all(item.get("status") == "ok" for item in components.values()) else "degraded"
    await _advisory_pressure(
        status,
        profile_dir if profile_dir else hermes_home,
        fts_rebuild=fts_rebuild,
        fts_precomputed=fts_precomputed,
    )

    status["profiles"] = scope_to_launch_profile(topology["profiles"])
    status["parked_profiles"] = scope_to_launch_profile(topology.get("parked_profiles", []))
    status["isolated_profile"] = isolated_launch_profile()
    status["gateway_mode"] = topology["gateway_mode"]
    status["multiplex_standalone_reason"] = topology.get("multiplex_standalone_reason")

    status["_gateway_pid"] = gateway["gateway_pid"]
    status["_gateway_health_url"] = gateway_health_url
    status["_gateways"] = topology["gateways"]
    return status


def _shape_status_response(canonical: dict[str, Any], signed_out: bool, verified: bool = True) -> dict[str, Any]:
    payload = copy.deepcopy(canonical)
    auth = _auth_gate_status()
    payload.update(auth)

    if not auth["auth_required"]:
        payload.update({
            "hermes_home": str(get_hermes_home()),
            "config_path": str(get_config_path()),
            "env_path": str(get_env_path()),
            "gateway_pid": payload.get("_gateway_pid"),
            "gateway_health_url": payload.get("_gateway_health_url"),
            "gateways": payload.get("_gateways") or [],
        })
    payload.pop("_gateway_pid", None)
    payload.pop("_gateway_health_url", None)
    payload.pop("_gateways", None)

    if not verified:
        redact_status_for_unverified(payload)
    if signed_out:
        return {key: value for key, value in payload.items() if key in _WEBAPP_SIGNED_OUT_STATUS_KEYS}
    return payload


_STATUS_BACKGROUND_TASKS: set[asyncio.Task] = set()


class _StatusCacheEntry:
    def __init__(self, key: tuple[str, str]):
        self.key = key
        self.lock = threading.Lock()
        self.state: Literal["EMPTY", "COMPUTING_COLD", "FRESH", "STALE_REFRESHING"] = "EMPTY"
        self.gen: int = 0
        self.ts: float = 0.0
        self.value: Optional[dict[str, Any]] = None
        self.fut: Optional[concurrent.futures.Future] = None
        self.task: Optional[asyncio.Task] = None
        self.cancel_event: threading.Event = threading.Event()
        self.last_error: Optional[Exception] = None


class StatusSnapshotProvider:
    def __init__(
        self,
        ttl_s: float = 3.0,
        cold_timeout_s: float = 10.0,
        swr_soft_deadline_s: float = 1.5,
        max_stale_s: float = 30.0,
    ):
        self.ttl_s = ttl_s
        self.cold_timeout_s = cold_timeout_s
        self.swr_soft_deadline_s = swr_soft_deadline_s
        self.max_stale_s = max_stale_s
        self._entries: dict[tuple[str, str], _StatusCacheEntry] = {}
        self._entries_lock = threading.Lock()
        self._collect_fn = _collect_canonical_status_snapshot

    def _get_entry(self, key: tuple[str, str]) -> _StatusCacheEntry:
        with self._entries_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _StatusCacheEntry(key)
                self._entries[key] = entry
            return entry

    def age_entries_for_test(self, seconds: float) -> None:
        with self._entries_lock:
            entries = list(self._entries.values())
        for entry in entries:
            with entry.lock:
                entry.ts -= seconds

    def inspect_entry_for_test(self, hermes_home: Path, requested_profile: str) -> dict[str, Any]:
        key = (str(hermes_home), requested_profile or "")
        with self._entries_lock:
            entry = self._entries.get(key)
        if entry is None:
            return {"state": "EMPTY", "gen": 0, "ts": 0.0, "in_flight": None}
        with entry.lock:
            return {
                "state": entry.state,
                "gen": entry.gen,
                "ts": entry.ts,
                "in_flight": entry.fut,
            }

    def invalidate(self, hermes_home: Optional[Path] = None) -> None:
        with self._entries_lock:
            if hermes_home is None:
                entries = list(self._entries.values())
            else:
                target_path = Path(hermes_home)
                entries = [e for k, e in self._entries.items() if Path(k[0]) == target_path]

        for e in entries:
            with e.lock:
                e.gen += 1
                e.state = "EMPTY"
                e.value = None
                e.ts = 0.0
                e.cancel_event.set()
                if e.task and not e.task.done():
                    e.task.cancel()
                if e.fut and not e.fut.done():
                    e.fut.cancel()

    async def _run_collector(
        self,
        entry: _StatusCacheEntry,
        gen: int,
        fut: concurrent.futures.Future,
        hermes_home: Path,
        requested_profile: str,
        profile_dir: Optional[Path],
        gateway_health_url: str,
    ) -> None:
        try:
            val = await self._collect_fn(hermes_home, requested_profile, profile_dir, gateway_health_url)
            with entry.lock:
                if entry.gen == gen and not entry.cancel_event.is_set():
                    entry.value = val
                    entry.ts = time.time()
                    entry.state = "FRESH"
                    entry.last_error = None
                    if not fut.done():
                        fut.set_result(val)
                else:
                    if entry.state == "COMPUTING_COLD" and entry.gen == gen:
                        entry.state = "EMPTY"
                    if not fut.done():
                        fut.cancel()
        except asyncio.CancelledError:
            with entry.lock:
                if entry.gen == gen:
                    if entry.state == "COMPUTING_COLD":
                        entry.state = "EMPTY"
                    elif entry.state == "STALE_REFRESHING":
                        entry.state = "FRESH"
                if not fut.done():
                    fut.cancel()
        except Exception as exc:
            with entry.lock:
                if entry.gen == gen and not entry.cancel_event.is_set():
                    entry.last_error = exc
                    if entry.state == "STALE_REFRESHING" and entry.value is not None:
                        entry.state = "FRESH"
                        if not fut.done():
                            fut.set_result(entry.value)
                    else:
                        entry.state = "EMPTY"
                        if not fut.done():
                            fut.set_exception(exc)
                else:
                    if not fut.done():
                        fut.cancel()
        finally:
            with entry.lock:
                if entry.fut is fut:
                    entry.fut = None
                if entry.task is not None and entry.task.done():
                    entry.task = None

    async def get_status_payload(
        self,
        hermes_home: Path,
        *,
        requested_profile: str,
        profile_dir: Optional[Path],
        gateway_health_url: str,
        signed_out: bool,
        verified: bool = True,
    ) -> dict[str, Any]:
        if _is_status_runtime_probe_monkeypatched():
            raw = await self._collect_fn(hermes_home, requested_profile, profile_dir, gateway_health_url)
            return _shape_status_response(raw, signed_out, verified=verified)

        key = (str(hermes_home), requested_profile or "")
        entry = self._get_entry(key)

        spawn = False
        gen = 0
        fut: Optional[concurrent.futures.Future] = None
        stale_val: Optional[dict[str, Any]] = None

        with entry.lock:
            now = time.time()
            age = (now - entry.ts) if entry.value is not None else float("inf")

            if entry.state == "FRESH" and age <= self.ttl_s and entry.value is not None:
                return _shape_status_response(entry.value, signed_out, verified=verified)

            # Preserve slot if worker is running: singleflight max_concurrent_workers_per_key == 1
            if entry.fut is not None and not entry.fut.done():
                fut = entry.fut
                spawn = False
                stale_val = entry.value if entry.state == "STALE_REFRESHING" else None
            elif entry.state == "EMPTY" or age > self.max_stale_s or entry.value is None:
                entry.state = "COMPUTING_COLD"
                entry.gen += 1
                gen = entry.gen
                fut = concurrent.futures.Future()
                entry.fut = fut
                entry.cancel_event = threading.Event()
                entry.last_error = None
                spawn = True
            elif entry.state == "FRESH":
                # Stale-While-Revalidate
                stale_val = entry.value
                entry.state = "STALE_REFRESHING"
                entry.gen += 1
                gen = entry.gen
                fut = concurrent.futures.Future()
                entry.fut = fut
                entry.cancel_event = threading.Event()
                entry.last_error = None
                spawn = True
            else:  # STALE_REFRESHING with prior fut completed
                stale_val = entry.value
                entry.gen += 1
                gen = entry.gen
                fut = concurrent.futures.Future()
                entry.fut = fut
                entry.cancel_event = threading.Event()
                entry.last_error = None
                spawn = True

        if spawn and fut is not None:
            task = asyncio.create_task(
                self._run_collector(
                    entry,
                    gen,
                    fut,
                    hermes_home,
                    requested_profile,
                    profile_dir,
                    gateway_health_url,
                )
            )
            _STATUS_BACKGROUND_TASKS.add(task)
            task.add_done_callback(_STATUS_BACKGROUND_TASKS.discard)
            with entry.lock:
                entry.task = task

        with entry.lock:
            curr_state = entry.state

        if curr_state in ("EMPTY", "COMPUTING_COLD"):
            if fut is None:
                raise HTTPException(status_code=503, detail="Status probe unavailable")
            try:
                val = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=self.cold_timeout_s)
                return _shape_status_response(val, signed_out, verified=verified)
            except TimeoutError:
                with entry.lock:
                    if entry.state == "COMPUTING_COLD" and entry.fut is fut:
                        entry.gen += 1
                        entry.state = "EMPTY"
                        if entry.task and not entry.task.done():
                            entry.task.cancel()
                        if not fut.done():
                            fut.cancel()
                raise HTTPException(status_code=503, detail="Status probe timed out")
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                t_end = time.time() + self.cold_timeout_s
                while time.time() < t_end:
                    await asyncio.sleep(0.02)
                    with entry.lock:
                        if entry.state == "FRESH" and entry.value is not None:
                            return _shape_status_response(entry.value, signed_out, verified=verified)
                        next_fut = entry.fut if (entry.fut is not None and not entry.fut.done() and entry.fut is not fut) else None
                    if next_fut is not None:
                        try:
                            val = await asyncio.wait_for(asyncio.wrap_future(next_fut), timeout=max(0.1, t_end - time.time()))
                            return _shape_status_response(val, signed_out, verified=verified)
                        except (asyncio.CancelledError, concurrent.futures.CancelledError):
                            continue
                        except TimeoutError:
                            break
                        except Exception:
                            break
                raise HTTPException(status_code=503, detail="Status probe cancelled")
        else:
            # SWR
            if stale_val is None:
                raise HTTPException(status_code=503, detail="Status probe unavailable")
            if fut is None:
                return _shape_status_response(stale_val, signed_out, verified=verified)
            try:
                val = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=self.swr_soft_deadline_s)
                return _shape_status_response(val, signed_out, verified=verified)
            except (TimeoutError, asyncio.CancelledError, concurrent.futures.CancelledError):
                return _shape_status_response(stale_val, signed_out, verified=verified)
            except Exception:
                return _shape_status_response(stale_val, signed_out, verified=verified)


STATUS_SNAPSHOT_PROVIDER = StatusSnapshotProvider()


def _reset_status_snapshot_cache() -> None:
    STATUS_SNAPSHOT_PROVIDER.invalidate()
    _reset_config_derived_cache()
    try:
        from hermes_cli.web_server_gateway import _reset_configured_platforms_cache
        _reset_configured_platforms_cache()
    except Exception as exc:
        import logging
        logging.getLogger("hermes.web.status").debug("platforms cache reset error: %s", exc)


@router.get("/api/status")
async def get_status(profile: Optional[str] = None,
                     request: Request = None):  # type: ignore[assignment]  # None when awaited in-process
    """Public machine-level liveness probe (``PUBLIC_API_PATHS``): version, gateway state,
    active session count and the auth-gate shape — no bodies, no session content, no secrets.

    ``?profile=`` (dashboard management switcher) uses the config-only contextvar scope, NOT
    _profile_scope: this handler awaits the remote health probe, and _profile_scope swaps
    process-global skills-module attributes a concurrent request would cross-restore.
    """
    from hermes_cli.web_server import _GATEWAY_HEALTH_URL
    verified = await _status_caller_verified(request)
    signed_out = _webapp_caller_signed_out(request, verified)
    status_scope = None
    # A signed-out caller's ``?profile=`` is ignored: an unknown name's 404 would tell it which exist.
    requested_profile = "" if signed_out else (profile or "").strip()
    profile_dir: Optional[Path] = None
    if requested_profile and requested_profile.lower() != "current":
        profile_dir = _resolve_profile_dir(requested_profile)
        status_scope = _config_profile_scope(requested_profile)
        status_scope.__enter__()

    try:
        home = get_hermes_home()
        return await STATUS_SNAPSHOT_PROVIDER.get_status_payload(
            home,
            requested_profile=requested_profile,
            profile_dir=profile_dir,
            gateway_health_url=_GATEWAY_HEALTH_URL,
            signed_out=signed_out,
            verified=verified,
        )
    finally:
        if status_scope is not None:
            status_scope.__exit__(*sys.exc_info())


@router.get("/api/system/stats")
def get_system_stats():  # sync: FastAPI runs it on a worker (psutil.cpu_percent blocks 100 ms)
    """Host + process system stats for the System page (stdlib identity; psutil CPU/memory/
    disk/uptime when available). Non-sensitive: no env values, no paths beyond hermes home."""
    import platform as _platform

    info: dict[str, Any] = {
        **_display_system_platform(
            system=_platform.system(), release=_platform.release(), version=_platform.version(),
            platform_label=_platform.platform()),
        "arch": _platform.machine(), "hostname": _platform.node(),
        "python_version": _platform.python_version(),
        "python_impl": _platform.python_implementation(),
        "hermes_version": get_version_info().base_version, "cpu_count": os.cpu_count()}

    def _disk():
        du = psutil.disk_usage(str(get_hermes_home()))
        info["disk"] = {"total": du.total, "used": du.used, "free": du.free, "percent": du.percent}

    def _cpu():
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        la = getattr(psutil, "getloadavg", None)
        if la:
            info["load_avg"] = list(la())

    def _uptime():
        info["uptime_seconds"] = int(time.time() - psutil.boot_time())

    def _process():
        proc = psutil.Process()
        info["process"] = {"pid": proc.pid, "rss": proc.memory_info().rss,
                           "create_time": int(proc.create_time()),
                           "num_threads": proc.num_threads()}

    # psutil enriches the picture when present; every probe below is optional.
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        info["memory"] = {"total": vm.total, "available": vm.available, "used": vm.used,
                          "percent": vm.percent}
        for fill in (_disk, _cpu, _uptime, _process):
            try:
                fill()
            except Exception:
                pass
        info["psutil"] = True
    except Exception:
        info["psutil"] = False
        # stdlib-only fallbacks for load average where the kernel exposes it.
        try:
            info["load_avg"] = list(os.getloadavg())
        except (OSError, AttributeError):
            pass

    return info


# Curator — background skill-maintenance status + the pause/resume/run-now controls.


@router.get("/api/curator")
async def get_curator_status(profile: Optional[str] = None):
    try:
        from agent import curator
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Curator unavailable: {exc}")

    def _run():
        state = _safe_call(curator, "load_state", {})
        return {
            "enabled": _safe_call(curator, "is_enabled", True),
            "paused": _safe_call(curator, "is_paused", False),
            "interval_hours": _safe_call(curator, "get_interval_hours", None),
            "last_run_at": state.get("last_run_at"),
            **{key: _safe_call(curator, f"get_{key}", None)
               for key in ("min_idle_hours", "stale_after_days", "archive_after_days")}}

    return await config_scoped_to_thread(profile, _run)


@router.put("/api/curator/paused")
async def set_curator_paused(body: CuratorPause, profile: Optional[str] = None):
    from agent import curator
    # ``_state_file()`` is ``get_hermes_home()/skills/.curator_state`` resolved at call
    # time, so the request's home override is what decides which profile pauses.
    await config_scoped_to_thread(profile, lambda: curator.set_paused(bool(body.paused)))
    return {"ok": True, "paused": bool(body.paused)}


def _spawn_action(argv: list, name: str, prefix: str, profile: Optional[str] = None) -> dict:
    """Spawn a background ``hermes -p <profile> <argv>`` action; a spawn failure is
    ``500 "<prefix>: <exc>"``."""
    try:
        proc = _spawn_hermes_action(_profile_cli_args(profile) + argv, name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{prefix}: {exc}")
    return {"ok": True, "pid": proc.pid, "name": name}


@router.post("/api/curator/run")
async def run_curator(profile: Optional[str] = None):
    """Trigger a curator review now (backgrounded; tail via action status). The curator
    archives and rewrites skills, so an unnamed target is refused while this backend
    serves several profiles."""
    return _spawn_action(["curator", "run"], "curator-run", "Failed to run curator",
                         destructive_profile(profile, "POST /api/curator/run"))


@router.get("/api/learning/graph")
async def get_learning_graph(profile: Optional[str] = None):
    """Learning graph for the desktop panel: profile-scoped learned skills + memory chunks."""
    def _run():
        from agent.learning_graph import build_learning_graph
        return build_learning_graph()

    try:
        # _profile_scope takes _SKILLS_PROFILE_LOCK and the graph build reads skills/memories
        # from disk — keep it off the event loop.
        return await scoped_to_thread(profile, _run)
    except HTTPException:
        raise  # an unknown ?profile= is the scope's 404, not a graph failure
    except Exception:
        _log.exception("GET /api/learning/graph failed")
        raise HTTPException(status_code=500, detail="Failed to build learning graph")


async def _learning_mutation(profile: Optional[str], fn, status: int, fallback: str):
    """Run a learning_mutations call under ``_profile_scope`` off-loop; a non-ok result
    becomes ``HTTPException(status, message)``."""
    res = await scoped_to_thread(profile, fn)
    if not res.get("ok"):
        raise HTTPException(status_code=status, detail=res.get("message", fallback))
    return res


@router.get("/api/learning/node")
async def get_learning_node(id: str, profile: Optional[str] = None):
    """Current content of a journey node (skill SKILL.md or memory chunk), for an edit prefill."""
    from agent.learning_mutations import node_detail
    return await _learning_mutation(profile, lambda: node_detail(id), 404, "not found")


@router.delete("/api/learning/node")
async def delete_learning_node(body: LearningNodeRef, profile: Optional[str] = None):
    """Delete a journey node — skills are archived (restorable), memories removed.

    ``?profile=`` is honoured too: a shared-backend Desktop scopes this call by query only, and
    ignoring it archived the same-named skill of the launch profile instead."""
    from agent.learning_mutations import delete_node
    return await _learning_mutation(
        body.profile or profile, lambda: delete_node(body.id), 400, "delete failed")


@router.put("/api/learning/node")
async def update_learning_node(body: LearningNodeEdit, profile: Optional[str] = None):
    """Rewrite a journey node's content (SKILL.md or memory chunk); profile as for DELETE."""
    from agent.learning_mutations import edit_node
    return await _learning_mutation(
        body.profile or profile, lambda: edit_node(body.id, body.content), 400, "edit failed")


# Portal — Nous Portal auth + Tool Gateway routing status (read-only).


@router.get("/api/portal")
async def get_portal_status(profile: Optional[str] = None):
    # load_config() + auth/subscription snapshots are disk reads on a polled endpoint —
    # keep them off the event loop.
    return await config_scoped_to_thread(profile, _get_portal_status_sync)


def _feature_state(feat) -> str:
    if getattr(feat, "managed_by_nous", False):
        return "via Nous Portal"
    if getattr(feat, "active", False):
        return getattr(feat, "current_provider", None) or "active"
    return "not configured"


def _get_portal_status_sync():
    cfg = load_config() or {}
    auth: dict[str, Any] = {}
    try:
        from hermes_cli.auth import get_nous_auth_status_local
        # Refresh-free snapshot so polling never performs an OAuth refresh.
        auth = get_nous_auth_status_local() or {}
    except Exception:
        auth = {}

    features = []
    try:
        from hermes_cli.nous_subscription import get_nous_subscription_features
        feats = get_nous_subscription_features(cfg)
        if feats is not None:
            features = [{"label": getattr(feat, "label", ""), "state": _feature_state(feat)}
                        for feat in feats.items()]
    except Exception:
        _log.exception("portal features failed")

    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    return {
        "logged_in": bool(auth.get("logged_in")), "portal_url": auth.get("portal_base_url"),
        "inference_url": auth.get("inference_base_url"),
        "provider": str((model_cfg or {}).get("provider") or ""),
        # Free tier: a token exists, so logged_in stays true for callers that only ask "is there a
        # credential"; surfaces that render an account must branch on free_tier first.
        "free_tier": bool(auth.get("free_tier")), "account_tier": auth.get("account_tier"),
        "subscription_url": "https://portal.nousresearch.com/manage-subscription",
        "features": features}


# Diagnostics: text-output actions spawned in the background, tailed via /api/actions/<name>.


@router.post("/api/ops/prompt-size")
async def run_prompt_size(profile: Optional[str] = None):
    return _spawn_action(["prompt-size"], "prompt-size", "Failed", profile)


@router.post("/api/ops/dump")
async def run_dump(profile: Optional[str] = None):
    return _spawn_action(["dump"], "dump", "Failed", profile)


@router.post("/api/ops/config-migrate")
async def run_config_migrate(profile: Optional[str] = None):
    return _spawn_action(["config", "migrate"], "config-migrate", "Failed", profile)


@router.post("/api/ops/debug-share")
async def run_debug_share_endpoint(body: DebugShareRequest | None = None,
                                   profile: Optional[str] = None):
    """Upload a redacted debug report + full logs and return the paste URLs; refused (400) unless
    the body carries ``consent: true`` from a user confirmation. Synchronous,
    unlike the other diagnostics actions: the point is the shareable URLs, returned as a
    structured payload the dashboard renders as copyable links."""
    from hermes_cli.debug import build_debug_share
    from hermes_cli.debug_redaction import redact_debug_support_text
    req = body or DebugShareRequest()
    if not req.consent:
        raise HTTPException(status_code=400,
                            detail="Debug share requires explicit consent (consent: true)")
    try:
        result = await config_scoped_to_thread(profile, lambda: build_debug_share(
            log_lines=max(1, min(int(req.lines), 5000)), redact=bool(req.redact)))
    except HTTPException:
        raise  # an unknown ?profile= is the scope's 404, not a failed share
    except RuntimeError as exc:
        # Required summary-report upload failed (offline / paste service down).
        error = redact_debug_support_text(exc)
        raise HTTPException(status_code=502, detail=f"Upload failed: {error}")
    except Exception as exc:
        _log.exception("debug share failed")
        error = redact_debug_support_text(exc)
        raise HTTPException(status_code=500, detail=f"Failed: {error}")

    return {"ok": True, "urls": result.urls, "failures": result.failures,
            "redacted": result.redacted, "auto_delete_seconds": result.auto_delete_seconds}


@logs_router.get("/api/logs")
async def get_logs(
    file: str = "agent", lines: int = 100, level: Optional[str] = None,
    component: Optional[str] = None, search: Optional[str] = None,
    profile: Optional[str] = None):
    from hermes_cli.logs import _read_tail, LOG_FILES
    log_name = LOG_FILES.get(file)
    if not log_name:
        raise HTTPException(status_code=400, detail=f"Unknown log file: {file}")
    with _config_profile_scope(profile):
        log_path = get_hermes_home() / "logs" / log_name
    if not log_path.exists():
        return {"file": file, "lines": []}

    try:
        from hermes_logging import COMPONENT_PREFIXES
    except ImportError:
        COMPONENT_PREFIXES = {}
    # "ALL"/"all"/empty → no filter (None, not (): _matches_filters treats an empty tuple as
    # "must match a prefix", which silently drops every line).
    min_level = level if level and level.upper() != "ALL" else None
    comp_prefixes = None
    if component and component.lower() != "all":
        comp_prefixes = COMPONENT_PREFIXES.get(component)
        if comp_prefixes is None:
            raise HTTPException(status_code=400, detail=f"Unknown component: {component}. "
                                f"Available: {', '.join(sorted(COMPONENT_PREFIXES))}")
    def _load_logs():
        result = _read_tail(
            log_path, min(lines, 500) if not search else 2000,
            has_filters=bool(min_level or comp_prefixes or search),
            min_level=min_level, component_prefixes=comp_prefixes)
        # _read_tail doesn't support free-text search, so post-filter (case-insensitive
        # substring) here and trim to the requested line count afterward.
        if search:
            needle = search.lower()
            result = [line for line in result if needle in line.lower()][-min(lines, 500):]
        return result

    result = await asyncio.to_thread(_load_logs)
    return {"file": file, "lines": result}
