"""Verified-handle bounded prerequisite reader (design D3a).

Opens each catalog-pinned resource once with platform reparse/no-follow controls,
verifies handle identity/type/size before reading any content bytes, enforces
hard per-resource and aggregate byte reservations without extra EOF probes,
rechecks handle metadata and SHA-256 before release, and closes every handle.
Never falls back to pathname readers, document extractors, or shell commands.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import errno
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
from threading import Lock
from typing import Any

from agent.delegation_context import TaskIntent
from agent.file_safety import (
    get_read_block_error,
    is_blocked_device_path,
    is_nt_namespace_path,
    local_drive_spelling,
)
from agent.redact import _is_secret_file_arg, redact_sensitive_text
from hermes_platform.host.local_volume import is_fixed_local_volume

from .catalog import MAX_FILE_BYTES, MAX_OUTPUT_BYTES, ResourcePin, _windows_reserved_component
from .descriptors import ReadDescriptor
from . import reservations

_READ_CHUNK_BYTES = 64 << 10
_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".pdf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".tar", ".gz",
    ".bz2", ".xz", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pyc", ".pyo", ".wasm", ".sqlite", ".sqlite3", ".db", ".mp3", ".mp4",
    ".wav", ".ogg", ".mov", ".avi", ".mkv", ".woff", ".woff2", ".ttf", ".otf",
})

_HANDLE_LOCK = Lock()
_ACTIVE_HANDLES = 0


@dataclass(slots=True)
class PhysicalReadMetrics:
    opens: int = 0
    bytes_read: int = 0


_METRICS: ContextVar[PhysicalReadMetrics | None] = ContextVar("prerequisite_read_metrics", default=None)


class BoundedReadError(RuntimeError):
    """Bounded nonsecret failure from verified-handle resource capture."""

    def __init__(self, code: str, message: str = "", *, in_flight: bool = False):
        super().__init__(message or code)
        self.code = code
        self.in_flight = in_flight


@dataclass(frozen=True, slots=True)
class PosixHandleOps:
    open: Callable[..., int]
    fstat: Callable[[int], Any]
    read: Callable[[int, int], bytes]
    close: Callable[[int], None]
    o_rdonly: int
    o_nofollow: int
    o_nonblock: int
    o_directory: int
    o_cloexec: int


@dataclass(frozen=True, slots=True)
class CapturedPrerequisites:
    descriptor: ReadDescriptor
    buffers: dict[str, bytes]


def active_handle_count() -> int:
    with _HANDLE_LOCK:
        return _ACTIVE_HANDLES


def _track_handle(delta: int) -> None:
    global _ACTIVE_HANDLES
    with _HANDLE_LOCK:
        _ACTIVE_HANDLES += delta


def _note_open() -> None:
    metrics = _METRICS.get()
    if metrics is not None:
        metrics.opens += 1


def _note_bytes(count: int) -> None:
    metrics = _METRICS.get()
    if metrics is not None:
        metrics.bytes_read += count


@contextmanager
def measure_physical_reads() -> Iterator[PhysicalReadMetrics]:
    metrics = PhysicalReadMetrics()
    token = _METRICS.set(metrics)
    try:
        yield metrics
    finally:
        _METRICS.reset(token)


def _precheck_raw_path(raw: str, *, posix_mode: bool, skip_host_denylist: bool) -> None:
    if not isinstance(raw, str) or not 1 <= len(raw) <= 1024 or any(ord(c) < 32 for c in raw):
        raise BoundedReadError("unsafe_path", "Invalid prerequisite resource path.")
    if is_nt_namespace_path(raw) or raw.startswith(("\\\\", "//")):
        raise BoundedReadError("unsafe_path", "Namespace and UNC paths are ineligible for prerequisite capture.")
    if is_blocked_device_path(raw):
        raise BoundedReadError("unsafe_path", "Device and procfs paths are ineligible for prerequisite capture.")
    if posix_mode:
        posix_path = PurePosixPath(raw)
        if not posix_path.is_absolute() or ".." in posix_path.parts:
            raise BoundedReadError("unsafe_path", "Prerequisite resource path must be canonical absolute.")
        suffix = posix_path.suffix.lower()
    else:
        win_path = PureWindowsPath(raw)
        if (win_path.drive.startswith(("\\\\", "//")) or ".." in win_path.parts
                or any(":" in p or "*" in p or "?" in p for p in win_path.parts if p != win_path.anchor)
                or (os.name == "nt" and any(
                    _windows_reserved_component(p) for p in win_path.parts if p != win_path.anchor
                ))):
            raise BoundedReadError("unsafe_path", "Unsupported Windows device or path syntax.")
        native = Path(raw)
        if not native.is_absolute():
            raise BoundedReadError("unsafe_path", "Prerequisite resource path must be absolute.")
        if os.name == "nt" and not is_fixed_local_volume(native.anchor):
            raise BoundedReadError("unsafe_path", "Non-local volume is ineligible for prerequisite capture.")
        suffix = native.suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        raise BoundedReadError("unsupported_binary_resource", "Binary or extracted document formats are ineligible.")
    if not skip_host_denylist and get_read_block_error(raw):
        raise BoundedReadError("denied_path", "Access denied by Hermes read policy.")


def _validate_pin_limits(pin: ResourcePin) -> int:
    if (type(pin.max_file_bytes) is not int or not 1 <= pin.max_file_bytes <= MAX_FILE_BYTES
            or type(pin.size) is not int or not 0 <= pin.size <= pin.max_file_bytes):
        raise BoundedReadError("oversize_resource", "Resource pin exceeds hard per-resource byte ceiling.")
    return pin.max_file_bytes


def _verify_opened_stat(st: Any, pin: ResourcePin, reserved_bytes: int) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise BoundedReadError("unsafe_path", "Opened descriptor is not a regular file.")
    if getattr(st, "st_file_attributes", 0) & 0x400:
        raise BoundedReadError("reparse_or_symlink", "Opened handle carries a reparse attribute.")
    if st.st_size > reserved_bytes or st.st_size > MAX_FILE_BYTES:
        raise BoundedReadError("oversize_resource", "Opened resource exceeds reserved byte limit.")
    if (st.st_dev, st.st_ino) != (pin.device, pin.inode):
        raise BoundedReadError("identity_mismatch", "Opened resource identity does not match catalog pin.")
    if st.st_size != pin.size:
        raise BoundedReadError("size_mismatch", "Opened resource size does not match catalog pin.")


def _bounded_stream_capture(
    fd: int,
    pin: ResourcePin,
    reserved_bytes: int,
    st_before: Any,
    *,
    read_fn: Callable[[int, int], bytes],
    fstat_fn: Callable[[int], Any],
    on_after_open: Callable[[], None] | None = None,
) -> bytes:
    if on_after_open is not None:
        on_after_open()
    buf = bytearray()
    while len(buf) < reserved_bytes:
        budget = min(_READ_CHUNK_BYTES, reserved_bytes - len(buf))
        chunk = read_fn(fd, budget)
        if not chunk:
            break
        if len(chunk) > budget:
            chunk = chunk[:budget]
        _note_bytes(len(chunk))
        buf.extend(chunk)
    st_after = fstat_fn(fd)
    if (st_after.st_dev, st_after.st_ino) != (pin.device, pin.inode):
        buf.clear()
        raise BoundedReadError("identity_mismatch", "Resource identity changed during capture.")
    before_mtime = getattr(st_before, "st_mtime_ns", None)
    after_mtime = getattr(st_after, "st_mtime_ns", None)
    if st_after.st_size != pin.size or len(buf) != pin.size or (
        before_mtime is not None and after_mtime is not None and before_mtime != after_mtime
    ):
        buf.clear()
        raise BoundedReadError("resource_changed_during_capture", "Resource size or mtime changed during capture.")
    if b"\x00" in buf:
        buf.clear()
        raise BoundedReadError("unsupported_binary_resource", "Binary content is ineligible for prerequisite capture.")
    if sha256(buf).hexdigest() != pin.sha256:
        buf.clear()
        raise BoundedReadError("digest_mismatch", "Captured content SHA-256 does not match catalog pin.")
    return bytes(buf)


def _check_windows_ancestors(target: Path, k32: Any) -> None:
    import ctypes
    import msvcrt

    FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
    OPEN_EXISTING = 3
    FLAGS = 0x02000000 | 0x00200000  # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
    invalid_ptr = ctypes.c_void_p(-1).value

    for parent in reversed(target.parents):
        raw_h = k32.CreateFileW(str(parent), 0, FILE_SHARE_ALL, None, OPEN_EXISTING, FLAGS, None)
        if raw_h in (None, -1, invalid_ptr):
            raise BoundedReadError("io_error", "Unable to verify ancestor directory handle.")
        _track_handle(1)
        try:
            fd = msvcrt.open_osfhandle(raw_h, os.O_RDONLY)
        except OSError as exc:
            k32.CloseHandle(raw_h)
            _track_handle(-1)
            raise BoundedReadError("io_error", "Unable to bind ancestor directory handle.") from exc
        try:
            st = os.fstat(fd)
            if getattr(st, "st_file_attributes", 0) & 0x400:
                raise BoundedReadError("reparse_or_symlink", "Ancestor directory is a reparse point.")
            if not stat.S_ISDIR(st.st_mode):
                raise BoundedReadError("unsafe_path", "Ancestor component is not a directory.")
        finally:
            os.close(fd)
            _track_handle(-1)


def _final_windows_path(k32: Any, raw_handle: int) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    length = k32.GetFinalPathNameByHandleW(raw_handle, buf, 1024, 0)
    if length == 0 or length >= 1024:
        raise BoundedReadError("io_error", "Unable to resolve final path from Windows handle.")
    final_raw = buf.value
    if is_nt_namespace_path(final_raw):
        raise BoundedReadError("unsafe_path", "Final handle path resolved to NT or UNC namespace.")
    final_local = local_drive_spelling(final_raw)
    if final_local.startswith(("\\\\", "//")):
        raise BoundedReadError("unsafe_path", "Final handle path resolved to UNC share.")
    return final_local


def _capture_windows_handle(
    pin: ResourcePin,
    reserved_bytes: int,
    *,
    skip_host_denylist: bool = False,
    on_after_open: Callable[[], None] | None = None,
    read_override: Callable[[int, int], bytes] | None = None,
) -> bytes:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    k32.GetFileType.restype = wintypes.DWORD
    k32.GetFileType.argtypes = [wintypes.HANDLE]
    k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    target = Path(pin.path)
    _check_windows_ancestors(target, k32)
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001  # Deny write and delete sharing for the capture lifetime.
    OPEN_EXISTING = 3
    FLAGS = 0x00200000 | 0x08000000  # FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN
    invalid_ptr = ctypes.c_void_p(-1).value

    _note_open()
    raw_h = k32.CreateFileW(str(target), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, FLAGS, None)
    if raw_h in (None, -1, invalid_ptr):
        err = ctypes.get_last_error()
        if err in (2, 3):
            raise BoundedReadError("io_error", "Pinned resource is absent.")
        raise BoundedReadError("io_error", "Unable to open verified Windows resource handle.")
    _track_handle(1)
    fd_owned = False
    try:
        if k32.GetFileType(raw_h) != 0x0001:  # FILE_TYPE_DISK
            raise BoundedReadError("unsafe_path", "Handle is not a disk file.")
        final_local = _final_windows_path(k32, raw_h)
        if not skip_host_denylist and get_read_block_error(final_local):
            raise BoundedReadError("denied_path", "Final handle path is denied by Hermes read policy.")
        if [p.casefold() for p in Path(final_local).parts] != [p.casefold() for p in target.parts]:
            raise BoundedReadError("reparse_or_symlink", "Final handle path diverged from pinned path.")
        fd = msvcrt.open_osfhandle(raw_h, os.O_RDONLY)
        fd_owned = True
    except BaseException:
        if not fd_owned:
            k32.CloseHandle(raw_h)
            _track_handle(-1)
        raise

    try:
        st_before = os.fstat(fd)
        _verify_opened_stat(st_before, pin, reserved_bytes)
        return _bounded_stream_capture(
            fd, pin, reserved_bytes, st_before,
            read_fn=read_override or os.read,
            fstat_fn=os.fstat,
            on_after_open=on_after_open,
        )
    finally:
        os.close(fd)
        _track_handle(-1)


def _default_posix_ops() -> PosixHandleOps:
    return PosixHandleOps(
        open=os.open,
        fstat=os.fstat,
        read=os.read,
        close=os.close,
        o_rdonly=os.O_RDONLY,
        o_nofollow=getattr(os, "O_NOFOLLOW", 0),
        o_nonblock=getattr(os, "O_NONBLOCK", 0),
        o_directory=getattr(os, "O_DIRECTORY", 0),
        o_cloexec=getattr(os, "O_CLOEXEC", 0),
    )


def _capture_posix_handle(
    pin: ResourcePin,
    reserved_bytes: int,
    *,
    ops: PosixHandleOps | None = None,
    on_after_open: Callable[[], None] | None = None,
    read_override: Callable[[int, int], bytes] | None = None,
) -> bytes:
    posix = ops or _default_posix_ops()
    if not posix.o_nofollow or not posix.o_directory:
        raise BoundedReadError("unsupported_backend", "POSIX O_NOFOLLOW/O_DIRECTORY support unavailable.")
    pure = PurePosixPath(pin.path)
    parts = pure.parts
    if not parts or parts[0] != "/" or len(parts) < 2:
        raise BoundedReadError("unsafe_path", "Invalid POSIX resource path.")

    _note_open()
    dir_fd = posix.open("/", posix.o_rdonly | posix.o_directory | posix.o_cloexec)
    _track_handle(1)
    try:
        for comp in parts[1:-1]:
            try:
                next_fd = posix.open(
                    comp,
                    posix.o_rdonly | posix.o_directory | posix.o_nofollow | posix.o_cloexec,
                    dir_fd=dir_fd,
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise BoundedReadError("reparse_or_symlink", "Symlink directory component rejected.") from exc
                raise BoundedReadError("io_error", "Directory traversal failed.") from exc
            _track_handle(1)
            posix.close(dir_fd)
            _track_handle(-1)
            dir_fd = next_fd
            st_dir = posix.fstat(dir_fd)
            if not stat.S_ISDIR(st_dir.st_mode):
                raise BoundedReadError("unsafe_path", "Intermediate component is not a directory.")

        try:
            leaf_fd = posix.open(
                parts[-1],
                posix.o_rdonly | posix.o_nofollow | posix.o_nonblock | posix.o_cloexec,
                dir_fd=dir_fd,
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise BoundedReadError("reparse_or_symlink", "Symlink target rejected.") from exc
            raise BoundedReadError("io_error", "Unable to open verified POSIX resource.") from exc
        _track_handle(1)
    finally:
        posix.close(dir_fd)
        _track_handle(-1)

    try:
        st_before = posix.fstat(leaf_fd)
        _verify_opened_stat(st_before, pin, reserved_bytes)
        return _bounded_stream_capture(
            leaf_fd, pin, reserved_bytes, st_before,
            read_fn=read_override or posix.read,
            fstat_fn=posix.fstat,
            on_after_open=on_after_open,
        )
    finally:
        posix.close(leaf_fd)
        _track_handle(-1)


def capture_verified_pin(
    pin: ResourcePin,
    *,
    posix_ops: PosixHandleOps | None = None,
    skip_host_denylist: bool = False,
    on_before_open: Callable[[], None] | None = None,
    on_after_open: Callable[[], None] | None = None,
    read_override: Callable[[int, int], bytes] | None = None,
) -> bytes:
    """Open and capture a single catalog-pinned resource from one verified handle."""
    reserved_bytes = _validate_pin_limits(pin)
    use_posix = posix_ops is not None or os.name != "nt"
    _precheck_raw_path(pin.path, posix_mode=use_posix, skip_host_denylist=skip_host_denylist)
    if on_before_open is not None:
        on_before_open()
    if use_posix:
        return _capture_posix_handle(
            pin, reserved_bytes, ops=posix_ops, on_after_open=on_after_open, read_override=read_override,
        )
    return _capture_windows_handle(
        pin, reserved_bytes,
        skip_host_denylist=skip_host_denylist,
        on_after_open=on_after_open,
        read_override=read_override,
    )


def _ensure_local_host_backend(task_id: str | None) -> None:
    try:
        from tools import file_tools_paths
        from tools.environments import sandbox_scope
    except ImportError as exc:
        raise BoundedReadError(
            "unsupported_backend",
            "Host backend verification modules are unavailable.",
        ) from exc
    if (
        sandbox_scope.hosted_scope() is not None
        or file_tools_paths._terminal_env_type_for_task(task_id or "default") != "local"
    ):
        raise BoundedReadError(
            "unsupported_backend",
            "Active file backend or tenant sandbox does not support host verified-handle capture.",
        )


def capture_descriptor_buffers(
    intent: TaskIntent,
    actor: str,
    call_id: str,
    tool: str,
    args: dict,
    *,
    task_id: str | None = None,
    posix_ops: PosixHandleOps | None = None,
    on_before_open: Callable[[], None] | None = None,
    on_after_open: Callable[[], None] | None = None,
    read_override: Callable[[int, int], bytes] | None = None,
) -> CapturedPrerequisites:
    """Execute serialized dispatch check, reserve bytes, and materialize verified buffers."""
    plan = reservations.begin_verified_read(intent, actor, call_id, tool, args)
    if plan.disposition != "started" or plan.descriptor is None:
        code_map = {
            "mismatch": "parameter_mismatch",
            "closed": "closed",
            "exhausted": "exhausted",
            "replay": "replay",
            "source_byte_budget_exhausted": "source_byte_budget_exhausted",
        }
        raise BoundedReadError(code_map.get(plan.disposition, "unavailable"), f"Prerequisite read {plan.disposition}.")

    fresh: dict[str, bytes] = {}
    err_code: str | None = None
    try:
        _ensure_local_host_backend(task_id)
        for pin in plan.uncached:
            fresh[pin.key] = capture_verified_pin(
                pin,
                posix_ops=posix_ops,
                on_before_open=on_before_open,
                on_after_open=on_after_open,
                read_override=read_override,
            )
    except BoundedReadError as exc:
        err_code = exc.code
        finish_disp = reservations.finish_verified_read(
            intent, actor, call_id, tool, plan.scope, None, error_code=err_code,
        )
        if finish_disp == "revoked_in_flight":
            raise BoundedReadError(
                "revoked_in_flight", "Assignment revoked while prerequisite read was in flight.", in_flight=True,
            ) from exc
        raise
    except OSError as exc:
        err_code = "io_error"
        finish_disp = reservations.finish_verified_read(
            intent, actor, call_id, tool, plan.scope, None, error_code=err_code,
        )
        if finish_disp == "revoked_in_flight":
            raise BoundedReadError(
                "revoked_in_flight", "Assignment revoked while prerequisite read was in flight.", in_flight=True,
            ) from exc
        raise BoundedReadError("io_error", "Verified prerequisite read I/O failed.") from exc

    finish_disp = reservations.finish_verified_read(
        intent, actor, call_id, tool, plan.scope, fresh, error_code=None,
    )
    if finish_disp == "revoked_in_flight":
        fresh.clear()
        raise BoundedReadError(
            "revoked_in_flight", "Assignment revoked while prerequisite read was in flight.", in_flight=True,
        )
    if finish_disp != "completed":
        fresh.clear()
        raise BoundedReadError(finish_disp, f"Prerequisite read settled as {finish_disp}.")
    return CapturedPrerequisites(plan.descriptor, {**plan.cached, **fresh})


def _effective_output_byte_ceiling() -> int:
    ceiling = MAX_OUTPUT_BYTES
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly()
        if isinstance(cfg, dict):
            char_cap = cfg.get("file_read_max_chars")
            if isinstance(char_cap, (int, float)) and not isinstance(char_cap, bool) and char_cap > 0:
                ceiling = min(ceiling, int(char_cap))
            section = cfg.get("tool_output")
            if isinstance(section, dict):
                byte_cap = section.get("max_bytes")
                if isinstance(byte_cap, (int, float)) and not isinstance(byte_cap, bool) and byte_cap > 0:
                    ceiling = min(ceiling, int(byte_cap))
    except (ImportError, OSError, ValueError):
        pass
    return max(1024, min(MAX_OUTPUT_BYTES, ceiling))


def _format_bounded_page(data: bytes, path: str, offset: int, limit: int) -> str:
    file_size = len(data)
    output_cap = _effective_output_byte_ceiling()
    if file_size == 0:
        return json.dumps(
            {"content": "", "total_lines": 0, "file_size": 0, "truncated": False, "hint": "File is empty (0 bytes)."},
            ensure_ascii=False,
        )
    text = data.decode("utf-8", errors="replace")
    text = text.removeprefix("\ufeff")
    ends_with_nl = text.endswith("\n")
    raw_lines = text[:-1].split("\n") if ends_with_nl else text.split("\n")
    total_lines = len(raw_lines)
    if offset > total_lines:
        return json.dumps(
            {
                "content": "",
                "total_lines": total_lines,
                "file_size": file_size,
                "truncated": False,
                "hint": f"Note: offset {offset} is beyond the end of the file ({total_lines} lines total).",
            },
            ensure_ascii=False,
        )
    end_index = min(total_lines, offset + limit - 1)
    width = len(str(end_index))
    # Reserve headroom for JSON envelope keys while guaranteeing UTF-8 result <= output_cap <= MAX_OUTPUT_BYTES.
    body_budget = max(256, output_cap - 512)
    formatted_lines: list[str] = []
    used_bytes = 0
    actual_end = offset - 1
    for lineno in range(offset, end_index + 1):
        raw_line = raw_lines[lineno - 1]
        if len(raw_line) > 4096:
            raw_line = raw_line[:4096] + "... [truncated]"
        redacted_line = redact_sensitive_text(raw_line, file_read=True, secret_file=_is_secret_file_arg(path))
        numbered = f"{lineno:>{width}}: {redacted_line}"
        encoded_len = len(numbered.encode("utf-8")) + (1 if formatted_lines else 0)
        if formatted_lines and used_bytes + encoded_len > body_budget:
            break
        if not formatted_lines and encoded_len > body_budget:
            clipped = numbered.encode("utf-8")[: max(64, body_budget - 32)].decode("utf-8", errors="ignore")
            formatted_lines.append(clipped)
            actual_end = lineno
            break
        formatted_lines.append(numbered)
        used_bytes += encoded_len
        actual_end = lineno

    truncated = actual_end < total_lines
    payload: dict[str, Any] = {
        "content": "\n".join(formatted_lines),
        "total_lines": total_lines,
        "file_size": file_size,
        "truncated": truncated,
    }
    if truncated:
        payload["next_offset"] = actual_end + 1
        payload["hint"] = (
            f"Use offset={actual_end + 1} to continue reading "
            f"(showing {offset}-{actual_end} of {total_lines} lines)"
        )
    serialized = json.dumps(payload, ensure_ascii=False)
    while len(serialized.encode("utf-8")) > output_cap and len(formatted_lines) > 1:
        formatted_lines.pop()
        actual_end -= 1
        payload["content"] = "\n".join(formatted_lines)
        payload["truncated"] = True
        payload["next_offset"] = actual_end + 1
        serialized = json.dumps(payload, ensure_ascii=False)
    return serialized


def dispatch_bounded_read_file(
    intent: TaskIntent,
    actor: str,
    call_id: str,
    args: dict,
    *,
    task_id: str | None = None,
    on_before_open: Callable[[], None] | None = None,
    on_after_open: Callable[[], None] | None = None,
) -> tuple[str, str | None]:
    """Dispatch an admitted ``read_file`` exclusively via the verified-handle reader."""
    try:
        captured = capture_descriptor_buffers(
            intent, actor, call_id, "read_file", args,
            task_id=task_id, on_before_open=on_before_open, on_after_open=on_after_open,
        )
    except BoundedReadError as exc:
        body = {
            "error": f"Prerequisite bounded read rejected ({exc.code}).",
            "disposition": exc.code,
            "in_flight": bool(exc.in_flight),
        }
        return json.dumps(body, ensure_ascii=False), f"prerequisite_{exc.code}"
    params = dict(captured.descriptor.parameters)
    pin = captured.descriptor.resources[0]
    offset = int(params.get("offset", 1))
    limit = int(params.get("limit", 2000))
    return _format_bounded_page(captured.buffers[pin.key], pin.path, offset, limit), None
