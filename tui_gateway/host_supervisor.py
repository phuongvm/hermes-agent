"""Supervisor for the dashboard compute-host child: with ``dashboard.turn_isolation``
agent turns run in one persistent ``python -m tui_gateway.compute_host`` child so heavy
agent threads do not contend with the serving process' event loop for the GIL."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from tools.environments.local import hermes_subprocess_env

logger = logging.getLogger(__name__)

MUTATOR_ROUTE_TABLE: dict[str, str] = {
    "prompt.submit": "turn-path", "session.interrupt": "turn-path", "reload.mcp": "run-concurrent",
    "session.save": "run-concurrent", "session.compress": "idle-gated",
    "prompt.submit.truncate": "idle-gated", "slash.model": "idle-gated",
    "slash.personality": "idle-gated", "slash.prompt": "idle-gated", "slash.compress": "idle-gated",
    "session.reset": "idle-gated", "session.history.reload": "idle-gated",
    "slash.retry": "idle-gated"}

_REGISTRY_NAME = "dashboard-compute-host.json"
_RESPAWN_WINDOW_SECS = 300.0
_SHUTDOWN_TIMEOUT_SECS = 10.0
# Late control-ack handlers: a compress that outlives its RPC waiter can run for the full
# compression ceiling plus a stall-fallback retry, so keep registrations past that — bounded.
# See #97948.
_LATE_CONTROL_TTL_SECS = 1800.0
_LATE_CONTROL_MAX = 64
# Host frames whose ``request_id`` resolves a pending/late control waiter.
_CONTROL_REPLY_TYPES = frozenset({
    "control.ack", "control.error", "respond.ack", "respond.error", "interrupt.ack",
    "reload_mcp.ack", "shutdown.ack"})


_append_log_lock = threading.Lock()


def append_log_record(path: str | Path, record: str) -> None:
    """Append one log record using O_APPEND and exactly one os.write call."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    text = record if record.endswith("\n") else f"{record}\n"
    with _append_log_lock:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, text.encode("utf-8", errors="replace"))
        finally:
            os.close(fd)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _check_output(argv: list[str], **kwargs: Any) -> str:
    """Stripped stdout of a short subprocess, or ``""`` on any failure."""
    with contextlib.suppress(Exception):
        return subprocess.check_output(
            argv, text=True, encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL,
            timeout=2, **kwargs).strip()
    return ""


def _build_sha() -> str:
    """HEAD sha or ``"unknown"``; shared with ``compute_host`` so the hello handshake agrees."""
    return _check_output(["git", "rev-parse", "HEAD"], cwd=str(_repo_root())) or "unknown"


def _call_logged(cb: Callable[[dict], None], frame: dict, failure: str) -> None:
    """Invoke a host-frame callback; a raising callback is logged, never propagated."""
    try:
        cb(frame)
    except Exception:
        logger.exception(failure)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception as exc:
        return isinstance(exc, PermissionError)


def _signal_pid(pid: int, sig: int, label: str) -> bool:
    """Send ``sig``; False when the pid is gone or the signal failed (logged)."""
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        logger.debug("failed to %s compute host pid=%s", label, pid, exc_info=True)
        return False


def _pid_command(pid: int) -> str:
    if pid <= 0:
        return ""
    with contextlib.suppress(Exception):  # Linux fast path
        data = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        if data:
            return data.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    return _check_output(["ps", "-p", str(pid), "-o", "command="])


def is_compute_host_identity(pid: int) -> bool:
    return "tui_gateway.compute_host" in _pid_command(pid)


def _create_supervisor_job_object() -> int | None:
    """Create a supervisor-owned Job Object configured with KILL_ON_JOB_CLOSE."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoCounters", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        hJob = ctypes.windll.kernel32.CreateJobObjectW(None, None)
        if not hJob:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        success = ctypes.windll.kernel32.SetInformationJobObject(
            hJob,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not success:
            ctypes.windll.kernel32.CloseHandle(hJob)
            return None
        return hJob
    except Exception as exc:
        logger.warning("failed to create Job Object: %s", exc)
        return None


def _assign_process_to_job(hJob: int, hProcess: int) -> bool:
    if sys.platform != "win32" or not hJob or not hProcess:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.AssignProcessToJobObject(hJob, hProcess))
    except Exception as exc:
        logger.warning("failed to assign process to Job Object: %s", exc)
        return False


def _is_process_in_job(hProcess: int, hJob: int) -> bool:
    if sys.platform != "win32" or not hJob or not hProcess:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        in_job = wintypes.BOOL()
        if ctypes.windll.kernel32.IsProcessInJob(hProcess, hJob, ctypes.byref(in_job)):
            return bool(in_job.value)
        return False
    except Exception:
        return False


def _resume_process(hProcess: int) -> bool:
    if sys.platform != "win32" or not hProcess:
        return False
    try:
        import ctypes
        res = ctypes.windll.ntdll.NtResumeProcess(hProcess)
        return res >= 0
    except Exception as exc:
        logger.warning("failed to resume suspended process: %s", exc)
        return False


def _open_process_handle(pid: int) -> int | None:
    if sys.platform != "win32" or pid <= 0:
        return None
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_SYNCHRONIZE = 0x00100000
        PROCESS_TERMINATE = 0x0001
        flags = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE | PROCESS_TERMINATE
        h = ctypes.windll.kernel32.OpenProcess(flags, False, pid)
        return h if h else None
    except Exception:
        return None


class CapacitySaturatedError(RuntimeError):
    def __init__(self, message: str, code: int = 5019, retry_after_ms: int = 1000):
        super().__init__(message)
        self.code = code
        self.retry_after_ms = retry_after_ms


class HostSupervisor:
    """Own one persistent compute-host child and relay its frames."""

    def __init__(
        self, *, registry_path: str | Path | None = None, argv: list[str] | None = None,
        cwd: str | Path | None = None, env: dict[str, str] | None = None,
        rpc_sink: Callable[[dict], None] | None = None, respawn_max: int = 3,
        heartbeat_secs: int = 15, expected_build_sha: str | None = None,
        expected_hermes_home: str | None = None, autostart: bool = True) -> None:
        self.registry_path = (
            Path(registry_path) if registry_path is not None
            else get_hermes_home() / "state" / _REGISTRY_NAME)
        self.argv = argv or [sys.executable, "-m", "tui_gateway.compute_host"]
        self.cwd = Path(cwd) if cwd is not None else _repo_root()
        self.env = env
        self.rpc_sink = rpc_sink or (lambda _obj: None)
        self.respawn_max = max(0, int(respawn_max))
        self.heartbeat_secs = max(1, int(heartbeat_secs))
        self.expected_build_sha = _build_sha() if expected_build_sha is None else expected_build_sha
        self.expected_hermes_home = (
            str(get_hermes_home()) if expected_hermes_home is None else expected_hermes_home)
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._hello_event = threading.Event()
        self._hello: dict[str, Any] = {}
        self._closing = False
        self._stopped_respawning = False
        self._restart_times: list[float] = []
        self._pending_turns: dict[str, tuple[str, Callable[[dict], None] | None]] = {}
        self._pending_controls: dict[str, queue.Queue[dict]] = {}
        # request_id -> (registered_at, handler) for control waiters that timed out while their
        # host work still runs, so the eventual control.ack is not silently dropped.
        # The host emits its control.ack whenever it finishes; without this the ack matched no queue and was
        # silently dropped. See #97948.
        self._late_control_handlers: dict[str, tuple[float, Callable[[dict], None]]] = {}
        self._stderr_tail: list[str] = []
        self._last_progress_counter = 0
        self.instance_id = uuid.uuid4().hex
        self._launcher_pid = 0
        self._host_pid = 0
        self._job_handle: int | None = None
        self._host_handle: int | None = None
        self._last_heartbeat_time = time.monotonic()
        self._pending_admissions: dict[str, queue.Queue[dict]] = {}
        if autostart:
            self.start()

    @property
    def launcher_pid(self) -> int:
        proc = self._proc
        return int(proc.pid or 0) if proc is not None else self._launcher_pid

    @property
    def host_pid(self) -> int:
        if self._host_pid > 0:
            return self._host_pid
        if self._hello:
            return int(self._hello.get("host_pid") or self.launcher_pid)
        return self.launcher_pid

    @property
    def pid(self) -> int:
        return self.host_pid or self.launcher_pid

    def is_running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None and not self._stopped_respawning

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            self._closing = False
            self.reconcile_startup_orphan()
            self._spawn_locked(reason="startup")

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
            proc = self._proc
            job_h = self._job_handle
            self._job_handle = None
            host_h = self._host_handle
            self._host_handle = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                self._send_frame({"type": "shutdown", "request_id": f"shutdown-{uuid.uuid4().hex}"})
                proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECS)
        except Exception:
            self._terminate_process(proc)
        finally:
            if sys.platform == "win32":
                with contextlib.suppress(Exception):
                    import ctypes
                    if host_h:
                        ctypes.windll.kernel32.CloseHandle(host_h)
                    if job_h:
                        ctypes.windll.kernel32.CloseHandle(job_h)
            self._remove_registry()

    def reconcile_startup_orphan(self) -> str:
        """Terminate a stale registered host, guarding against PID reuse."""
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "none"
        except Exception:
            data = None
        if not isinstance(data, dict):
            self._remove_registry()
            return "invalid-registry"

        parent_pid = int(data.get("parent_pid") or 0)
        # Reject a second live instance without signaling or killing the active instance
        if parent_pid > 0 and _pid_alive(parent_pid) and parent_pid != os.getpid():
            raise RuntimeError(
                f"Active gateway/supervisor instance already running (parent_pid={parent_pid}, instance_id={data.get('instance_id')})"
            )

        try:
            pid = int((data or {}).get("host_pid") or 0)
        except Exception:
            pid = 0
        if pid <= 0 or not _pid_alive(pid):
            outcome = "not-running"
        elif not self._pid_matches_compute_host(pid):
            outcome = "pid-reuse-ignored"  # PID reused by another process: never signal it
        else:
            self._terminate_pid(pid, timeout=_SHUTDOWN_TIMEOUT_SECS)
            outcome = "terminated"
        self._remove_registry()
        return outcome

    def submit_turn(self, frame: dict[str, Any], *, on_complete: Callable[[dict], None] | None = None) -> str:
        self.start()
        request_id = str(frame.get("request_id") or uuid.uuid4().hex)
        sid = str(frame.get("sid") or "")
        payload = {**frame, "type": "turn.start", "request_id": request_id}
        admission_q: queue.Queue[dict] = queue.Queue(maxsize=1)
        with self._lock:
            self._pending_turns[request_id] = (sid, on_complete)
            self._pending_admissions[request_id] = admission_q
        try:
            self._send_frame(payload)
            try:
                ack = admission_q.get(timeout=2.0)
                if ack.get("type") == "turn.error":
                    msg = ack.get("message", "capacity saturated")
                    code = int(ack.get("code") or 5019)
                    retry_after = int(ack.get("retry_after_ms") or 1000)
                    raise CapacitySaturatedError(f"Capacity saturated: {msg}", code=code, retry_after_ms=retry_after)
            except queue.Empty:
                pass
        except Exception as exc:
            with self._lock:
                self._pending_turns.pop(request_id, None)
                self._pending_admissions.pop(request_id, None)
            if on_complete is not None:
                on_complete({"type": "turn.error", "sid": sid, "request_id": request_id,
                             "reason": "send_failed", "message": str(exc),
                             "execution_state": "not_started"})
            raise
        finally:
            with self._lock:
                self._pending_admissions.pop(request_id, None)
        return request_id

    def interrupt(self, sid: str, *, request_id: str | None = None) -> None:
        self.start()
        self._send_frame(
            {"type": "interrupt", "sid": sid, "request_id": request_id or uuid.uuid4().hex})

    def _await_reply(self, frame: dict[str, Any], request_id: str, timeout: float) -> dict:
        """Send ``frame`` and block for the host reply carrying ``request_id``."""
        q: queue.Queue[dict] = queue.Queue(maxsize=1)
        with self._lock:
            self._pending_controls[request_id] = q
        try:
            self._send_frame(frame)
            return q.get(timeout=timeout)
        finally:
            with self._lock:
                self._pending_controls.pop(request_id, None)

    def respond(self, sid: str, params: dict[str, Any], *, timeout: float = 15.0) -> dict:
        """Deliver an interactive prompt response to the host that owns it."""
        self.start()
        request_id = uuid.uuid4().hex
        frame = {"type": "respond", "sid": sid, "request_id": request_id, "params": dict(params)}
        return self._await_reply(frame, request_id, timeout)

    def reload_mcp(self, sid: str, *, request_id: str | None = None) -> dict:
        payload = {"type": "reload_mcp", "sid": sid, "request_id": request_id or uuid.uuid4().hex}
        return self.control(sid, route_name="reload.mcp", wait=True, payload=payload)

    def control(
        self, sid: str, *, route_name: str, payload: dict[str, Any] | None = None,
        wait: bool = True, timeout: float = 30.0, on_late_ack: Callable[[dict], None] | None = None,
    ) -> dict:
        """Send a control frame; with ``wait`` block up to ``timeout`` for its ack. ``on_late_ack``
        (only with ``wait``) keeps the request adoptable after the waiter gives up: the host's
        eventual ``control.ack``/``control.error``/``error`` fires it once (bounded by
        ``_LATE_CONTROL_TTL_SECS``/``_MAX``) instead of being dropped."""
        if route_name not in MUTATOR_ROUTE_TABLE:
            raise ValueError(f"unclassified host mutator route: {route_name}")
        self.start()
        payload = payload or {}
        request_id = str(payload.get("request_id") or uuid.uuid4().hex)
        frame = {"type": "control", **payload, "sid": sid, "route_name": route_name,
                 "request_id": request_id}
        if not wait:
            self._send_frame(frame)
            return {"status": "sent", "request_id": request_id}
        try:
            return self._await_reply(frame, request_id, timeout)
        except queue.Empty:
            if on_late_ack is not None:
                self._register_late_control_handler(request_id, on_late_ack)
            raise

    def _register_late_control_handler(self, request_id: str, handler: Callable[[dict], None]) -> None:
        now = time.monotonic()
        with self._lock:
            handlers = self._late_control_handlers
            for rid in [r for r, (at, _cb) in handlers.items() if now - at >= _LATE_CONTROL_TTL_SECS]:
                handlers.pop(rid, None)
            while len(handlers) >= _LATE_CONTROL_MAX:
                handlers.pop(min(handlers, key=lambda rid: handlers[rid][0]), None)
            handlers[request_id] = (now, handler)

    def _deliver_control_frame(self, request_id: str, frame: dict[str, Any]) -> None:
        with self._lock:
            q = self._pending_controls.get(request_id)
            late = None if q is not None else self._late_control_handlers.pop(request_id, None)
        if q is not None:
            with contextlib.suppress(queue.Full):
                q.put_nowait(frame)
        elif late is not None:
            _call_logged(late[1], frame, f"compute host late control ack handler failed (request_id={request_id})")

    def _spawn_locked(self, *, reason: str) -> None:
        if self._stopped_respawning:
            raise RuntimeError("compute host respawn disabled after crash loop")
        self._hello_event.clear()
        self._hello = {}
        self._last_heartbeat_time = time.monotonic()
        env = {**hermes_subprocess_env(inherit_credentials=True), **os.environ, **(self.env or {})}
        env["HERMES_COMPUTE_HOST_HEARTBEAT_SECS"] = str(self.heartbeat_secs)
        root = str(_repo_root())
        env.setdefault("PYTHONPATH", root)
        if root not in env["PYTHONPATH"].split(os.pathsep):
            env["PYTHONPATH"] = root + os.pathsep + env["PYTHONPATH"]

        job_handle = None
        creationflags = 0
        if sys.platform == "win32":
            job_handle = _create_supervisor_job_object()
            if job_handle:
                creationflags |= 0x00000004  # CREATE_SUSPENDED

        # Lossy UTF-8 decode: a locale-mismatched byte must not raise inside the drain threads.
        proc = subprocess.Popen(
            self.argv, cwd=str(self.cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=(sys.platform != "win32"), creationflags=creationflags)
        self._proc = proc
        self._launcher_pid = int(proc.pid or 0)
        self._job_handle = job_handle

        if sys.platform == "win32" and job_handle:
            _assign_process_to_job(job_handle, int(proc._handle))
            _is_process_in_job(int(proc._handle), job_handle)
            _resume_process(int(proc._handle))

        for target, name in ((self._drain_stdout, "compute-host-stdout"),
                             (self._drain_stderr, "compute-host-stderr"),
                             (self._wait_for_exit, "compute-host-wait"),
                             (self._heartbeat_watchdog_loop, "compute-host-watchdog")):
            threading.Thread(target=target, args=(proc,), name=name, daemon=True).start()
        if not self._hello_event.wait(timeout=10.0):
            self._terminate_process(proc)
            raise RuntimeError(f"compute host did not send hello; stderr={self._stderr_tail[-5:]}")
        self._validate_hello()
        self._persist_registry()
        logger.info("compute host started launcher_pid=%s host_pid=%s reason=%s",
                    self.launcher_pid, self.host_pid, reason)

    def _validate_hello(self) -> None:
        hello = self._hello
        if not hello:
            raise RuntimeError("compute host missing hello")
        pv = hello.get("protocol_version")
        if pv is not None and pv != 1:
            raise RuntimeError(f"compute host protocol version mismatch: {pv} != 1")
        got_home = str(hello.get("hermes_home") or "")
        if got_home and got_home != self.expected_hermes_home:
            raise RuntimeError(
                f"compute host HERMES_HOME mismatch: {got_home} != {self.expected_hermes_home}")
        got_sha = str(hello.get("build_sha") or "")
        expected = self.expected_build_sha
        if expected != "unknown" and got_sha not in {"", "unknown", expected}:
            raise RuntimeError(f"compute host build mismatch: {got_sha} != {expected}")
        host_pid = int(hello.get("host_pid") or 0)
        self._host_pid = host_pid or self.launcher_pid
        if sys.platform == "win32" and self._job_handle and host_pid > 0:
            if host_pid != self.launcher_pid:
                hHost = _open_process_handle(host_pid)
                if hHost:
                    if not _is_process_in_job(hHost, self._job_handle):
                        if self._proc:
                            self._terminate_process(self._proc)
                        raise RuntimeError(f"compute host reported pid {host_pid} is not in supervisor Job Object")
                    self._host_handle = hHost

    def _persist_registry(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path.with_suffix(self.registry_path.suffix + ".tmp")
        payload = {
            "parent_pid": os.getpid(),
            "launcher_pid": self.launcher_pid,
            "host_pid": self.host_pid,
            "boot_id": self._hello.get("boot_id") or "",
            "resolved_home": self.expected_hermes_home,
            "instance_id": self.instance_id,
            "build_sha": self._hello.get("build_sha") or "",
            "started_at": time.time(),
            "argv": self.argv,
        }
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp.replace(self.registry_path)

    def _remove_registry(self) -> None:
        try:
            self.registry_path.unlink(missing_ok=True)
        except Exception:
            logger.debug("failed to remove compute host registry", exc_info=True)

    def _send_frame(self, frame: dict[str, Any]) -> None:
        frame.setdefault("protocol_version", 1)
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None:
                raise RuntimeError("compute host is not running")
            proc.stdin.write(json.dumps(frame, separators=(",", ":"), ensure_ascii=False) + "\n")
            proc.stdin.flush()

    def _drain_stdout(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        stream = proc.stdout
        max_line_len = 1024 * 1024
        while True:
            line = stream.readline(max_line_len + 1)
            if not line:
                break
            if len(line.encode("utf-8", errors="replace")) > max_line_len and not line.endswith("\n"):
                logger.error("compute host frame exceeded 1 MiB limit")
                while not line.endswith("\n"):
                    chunk = stream.readline(max_line_len)
                    if not chunk:
                        break
                    line = chunk
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("compute host emitted invalid json: %r", line[:200])
                continue
            if isinstance(frame, dict):
                self._handle_host_frame(frame)

    def _drain_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            if text := raw.rstrip("\n"):
                self._stderr_tail = (self._stderr_tail + [text])[-80:]
                logger.warning("compute host stderr: %s", text)

    def _handle_host_frame(self, frame: dict[str, Any]) -> None:
        ftype = str(frame.get("type") or "")
        request_id = str(frame.get("request_id") or "")
        if ftype in _CONTROL_REPLY_TYPES or (ftype == "error" and request_id):
            self._deliver_control_frame(request_id, frame)
        elif ftype == "hello":
            self._hello = dict(frame)
            self._hello_event.set()
            with contextlib.suppress(Exception):
                self._send_frame({"type": "hello.ack", "protocol_version": 1, "boot_id": frame.get("boot_id")})
        elif ftype == "turn.accepted":
            with self._lock:
                q = self._pending_admissions.get(request_id)
            if q is not None:
                with contextlib.suppress(queue.Full):
                    q.put_nowait(frame)
        elif ftype == "hb":
            self._last_heartbeat_time = time.monotonic()
            self._last_progress_counter = int(frame.get("progress_counter") or self._last_progress_counter)
            logger.debug("compute host heartbeat: %s", frame)
        elif ftype == "rpc":
            if isinstance(frame.get("message"), dict):
                self.rpc_sink(frame["message"])
        elif ftype in ("turn.end", "turn.error"):
            with self._lock:
                q = self._pending_admissions.get(request_id)
            if q is not None:
                with contextlib.suppress(queue.Full):
                    q.put_nowait(frame)
            with self._lock:
                pending = self._pending_turns.pop(request_id, None)
            if pending is not None and pending[1] is not None:
                _call_logged(pending[1], frame, "compute host turn completion callback failed")

    def _heartbeat_watchdog_loop(self, proc: subprocess.Popen[str]) -> None:
        deadline_secs = 3.0 * self.heartbeat_secs
        while not self._closing:
            time.sleep(min(1.0, float(self.heartbeat_secs)))
            if self._proc is not proc or proc.poll() is not None:
                break
            if time.monotonic() - self._last_heartbeat_time > deadline_secs:
                logger.error("compute host missed heartbeats (deadline %.1fs exceeded); terminating unresponsive host", deadline_secs)
                self._terminate_process(proc)
                break

    def _wait_for_exit(self, proc: subprocess.Popen[str]) -> None:
        code = proc.wait()
        if self._closing:
            return
        with self._lock:
            if self._proc is not proc:
                return
            self._proc = None
        self._remove_registry()
        self._fail_pending_turns(reason="crash", message=f"compute host exited with code {code}")
        self._maybe_respawn_after_crash()

    def _fail_pending_turns(self, *, reason: str, message: str) -> None:
        with self._lock:
            pending = self._pending_turns
            self._pending_turns = {}
        failure = {"reason": reason, "message": message, "execution_state": "unknown"}
        for request_id, (sid, cb) in pending.items():
            self.rpc_sink({"jsonrpc": "2.0", "method": "event",
                           "params": {"type": "error", "session_id": sid, "payload": dict(failure)}})
            if cb is not None:
                frame = {"type": "turn.error", "sid": sid, "request_id": request_id, **failure}
                _call_logged(cb, frame, "compute host error callback failed")
        # A crashed host never emits the late acks timed-out control waiters still expect; fail
        # them too so the client's "still running" notice can't hang.
        with self._lock:
            late = self._late_control_handlers
            self._late_control_handlers = {}
        for request_id, (_registered_at, handler) in late.items():
            frame = {"type": "control.error", "request_id": request_id, **failure}
            _call_logged(handler, frame, "compute host late control error handler failed")

    def _maybe_respawn_after_crash(self) -> None:
        now = time.monotonic()
        self._restart_times = [t for t in self._restart_times if now - t <= _RESPAWN_WINDOW_SECS]
        if len(self._restart_times) >= self.respawn_max:
            self._stopped_respawning = True
            logger.error(
                "compute host crash loop: max %s restarts per 5min reached; not respawning",
                self.respawn_max)
            return
        self._restart_times.append(now)
        # Small bounded backoff; tests and first recovery stay quick.
        delay = min(5.0, 0.25 * (2 ** max(0, len(self._restart_times) - 1)))

        def _respawn() -> None:
            time.sleep(delay)
            with self._lock:
                if self._closing or self._stopped_respawning or self._proc is not None:
                    return
                try:
                    self._spawn_locked(reason="crash")
                except Exception:
                    logger.exception("compute host respawn failed")
        threading.Thread(target=_respawn, name="compute-host-respawn", daemon=True).start()

    _pid_matches_compute_host = staticmethod(is_compute_host_identity)

    def _terminate_pid(self, pid: int, *, timeout: float = _SHUTDOWN_TIMEOUT_SECS) -> None:
        if not _signal_pid(pid, signal.SIGTERM, "SIGTERM"):
            return
        deadline = time.monotonic() + timeout
        while _pid_alive(pid):
            if time.monotonic() >= deadline:
                _signal_pid(pid, signal.SIGKILL, "SIGKILL")
                return
            time.sleep(0.05)

    def _terminate_process(self, proc: subprocess.Popen[str]) -> None:
        if sys.platform == "win32" and self._job_handle:
            with contextlib.suppress(Exception):
                import ctypes
                ctypes.windll.kernel32.TerminateJobObject(self._job_handle, 1)
        if proc.poll() is not None:
            return
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECS)
            return
        for step in (proc.kill, lambda: proc.wait(timeout=2)):
            with contextlib.suppress(Exception):
                step()


__all__ = ["MUTATOR_ROUTE_TABLE", "HostSupervisor", "append_log_record", "is_compute_host_identity"]
