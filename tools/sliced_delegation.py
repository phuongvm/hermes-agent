"""``SlicedDelegationProtocol`` (Slice 4 / Module D3): the one place the bounded-slice delegation
contract lives, so ``delegate_tool``, ``delegate_tool_child_run``, ``turn_liveness``,
``chat_completion_stream_monitor`` and ``hermes_cli.web_server`` import constants and helpers from
here instead of each re-deriving them.

Contract (design.md §5.3):
  * ``MAX_SLICE_TIMEOUT_S = 1800`` — one delegated slice is bounded to 30 minutes; a caller asking for
    more is refused at preflight (``validate_slice_timeout``), never silently clamped.
  * ``slice{N}_manifest.json`` — a child that carries a slice id writes a durable disk checkpoint
    (``write_slice_manifest``: temp file + ``os.fsync`` + ``os.replace``) before its result returns.
  * ``subagent.heartbeat`` every ``60s`` (``SubagentHeartbeat``) so the parent/gateway sees a live child
    even while it waits on a multi-minute completion.
  * silent SSE stream (``0`` chunks) is cut over after ``180s`` (``should_cut_over_silent_stream``).
  * synchronous git/sqlite/filesystem probes are offloaded onto a bounded 4-worker pool
    (``offload_probe``) so the dashboard event loop never blocks on them (stall p99 ``< 1.0s``).

Stdlib only; no agent or FastAPI imports (importable from any of the five consumers without cycles).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

MAX_SLICE_TIMEOUT_S = 1800
HEARTBEAT_INTERVAL_S = 60.0
HEARTBEAT_EVENT = "subagent.heartbeat"
SILENT_STREAM_CUTOVER_S = 180.0
PROBE_POOL_MAX_WORKERS = 4
CANONICAL_SLICE_IDS: tuple[str, ...] = ("slice1", "slice2", "slice3", "slice4")


# --------------------------------------------------------------------------------------------- timeout bound
def validate_slice_timeout(timeout_s: Any) -> Optional[str]:
    """``None`` when *timeout_s* is absent or within ``MAX_SLICE_TIMEOUT_S``; else the refusal message.

    Fail closed on non-numeric input: a typo must not become an unbounded delegation."""
    if timeout_s is None:
        return None
    if isinstance(timeout_s, bool):
        return f"timeout_s must be a number of seconds <= {MAX_SLICE_TIMEOUT_S}, got {timeout_s!r}."
    try:
        value = float(timeout_s)
    except (TypeError, ValueError):
        return f"timeout_s must be a number of seconds <= {MAX_SLICE_TIMEOUT_S}, got {timeout_s!r}."
    if value != value or value <= 0:  # NaN / non-positive
        return f"timeout_s must be a positive number of seconds <= {MAX_SLICE_TIMEOUT_S}, got {timeout_s!r}."
    if value > MAX_SLICE_TIMEOUT_S:
        return (
            f"timeout_s={value:g} exceeds the per-slice bound of {MAX_SLICE_TIMEOUT_S}s (30 minutes). "
            f"Split the work into independent slices (slice1..slice4) of <= {MAX_SLICE_TIMEOUT_S}s each; "
            f"monolithic multi-slice delegations are refused at preflight."
        )
    return None


# --------------------------------------------------------------------------------------------- manifest
@dataclasses.dataclass(frozen=True)
class SliceManifest:
    """Durable per-slice checkpoint. ``timestamp_utc`` defaults to now (UTC, ``Z``-suffixed)."""

    slice_id: str
    repo_root: str
    head_sha: str
    modified_files_sha256: Mapping[str, str]
    proof_commands: Sequence[str]
    proof_exit_code: int
    timestamp_utc: str = ""

    def __post_init__(self) -> None:
        if self.slice_id not in CANONICAL_SLICE_IDS:
            raise ValueError(f"slice_id must be one of {CANONICAL_SLICE_IDS}, got {self.slice_id!r}")
        if not self.timestamp_utc:
            object.__setattr__(self, "timestamp_utc", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    @property
    def filename(self) -> str:
        return f"{self.slice_id}_manifest.json"

    def to_json(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id, "repo_root": self.repo_root, "head_sha": self.head_sha,
            "modified_files_sha256": dict(self.modified_files_sha256), "proof_commands": list(self.proof_commands),
            "proof_exit_code": int(self.proof_exit_code), "timestamp_utc": self.timestamp_utc,
        }


def write_slice_manifest(directory: Path, manifest: SliceManifest) -> Path:
    """Write ``<directory>/<slice_id>_manifest.json`` durably: temp file, ``os.fsync``, ``os.replace``.

    Returns the final path. The fsync happens BEFORE the rename so a crash can never leave a
    half-written manifest at the canonical name."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / manifest.filename
    tmp = directory / f".{manifest.filename}.tmp.{os.getpid()}.{threading.get_ident()}"
    payload = json.dumps(manifest.to_json(), indent=2, sort_keys=True) + "\n"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


# --------------------------------------------------------------------------------------------- heartbeat
class SubagentHeartbeat:
    """Emit ``subagent.heartbeat`` once per ``interval_s`` of elapsed wall time.

    ``tick(now)`` is pure bookkeeping (callers drive it from whatever scheduler they already run) and
    returns ``True`` exactly when an event was emitted. ``emit`` has the ``_safe_progress`` shape:
    ``emit(kind, **fields)``."""

    def __init__(self, emit: Callable[..., Any], *, subagent_id: Optional[str], interval_s: float = HEARTBEAT_INTERVAL_S,
                 started_at: Optional[float] = None):
        self._emit = emit
        self.subagent_id = subagent_id
        self.interval_s = float(interval_s)
        # Anchored on the first ``tick()`` when not given, so callers that drive a synthetic clock and callers
        # that pass ``time.monotonic()`` both measure elapsed time from the same origin.
        self.started_at: Optional[float] = None if started_at is None else float(started_at)
        self._last_emit: Optional[float] = None
        self.emitted = 0

    def tick(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else float(now)
        if self.started_at is None:
            self.started_at = now
        anchor = self.started_at if self._last_emit is None else self._last_emit
        if now - anchor < self.interval_s:
            return False
        self._last_emit = now
        self.emitted += 1
        try:
            self._emit(HEARTBEAT_EVENT, subagent_id=self.subagent_id, elapsed_s=round(now - self.started_at, 3),
                       interval_s=self.interval_s, beat=self.emitted)
        except Exception:
            pass  # a progress sink failure must never end a healthy child
        return True


def subagent_heartbeat(emit: Callable[..., Any], *, subagent_id: Optional[str],
                       interval_s: float = HEARTBEAT_INTERVAL_S) -> SubagentHeartbeat:
    """Build the 60s ``subagent.heartbeat`` emitter for one delegated child (``emit(kind, **fields)``)."""
    return SubagentHeartbeat(emit, subagent_id=subagent_id, interval_s=interval_s)


# --------------------------------------------------------------------------------------------- silent stream
def should_cut_over_silent_stream(*, last_chunk_at: float, now: float, chunks_delivered: int,
                                  cutover_s: float = SILENT_STREAM_CUTOVER_S) -> bool:
    """``True`` when an upstream SSE stream delivered ``0`` chunks for ``cutover_s`` seconds.

    Streams that already delivered chunks are governed by the provider stale timer (their patience may
    legitimately be longer for reasoning models); the silent-cutover rule is strictly for the start-to-first-chunk
    gap."""
    if chunks_delivered > 0:
        return False
    return (now - last_chunk_at) > cutover_s


# --------------------------------------------------------------------------------------------- probe offload
_POOL_LOCK = threading.Lock()
_POOL: Optional[concurrent.futures.ThreadPoolExecutor] = None


def probe_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Process-wide bounded pool (``max_workers=4``) for synchronous git/sqlite/filesystem probes."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = concurrent.futures.ThreadPoolExecutor(
                max_workers=PROBE_POOL_MAX_WORKERS, thread_name_prefix="hermes-probe")
        return _POOL


async def offload_probe(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking probe on ``probe_pool()`` and await it without stalling the event loop.

    ``asyncio.to_thread`` semantics (contextvars propagate) on the bounded pool rather than the loop's default
    executor, so a burst of slow probes can never exhaust the executor the rest of the app relies on."""
    import contextvars

    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(probe_pool(), lambda: ctx.run(fn, *args, **kwargs))
