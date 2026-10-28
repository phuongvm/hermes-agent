"""The dashboard session token: where its value comes from.

The desktop shell mints the token via HERMES_DASHBOARD_SESSION_TOKEN; a
"file:<path>" value instead names a file to read it from (the Nix modules
render backend.sessionTokenFile this way, so a path -- not a secret -- sits
in the service environment). With no variable, a durable on-disk fallback in
$HERMES_HOME/.dashboard_session_token (0o600 / Windows DACL) is used to prevent
spurious 401 re-auth pop-ups across daemon restarts. It dies with the process
and is injected into the SPA HTML so only the web UI can use it.
"""
from __future__ import annotations

import contextlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# Session token for sensitive endpoints. The desktop shell mints it via
# HERMES_DASHBOARD_SESSION_TOKEN; otherwise durable on-disk fallback in
# $HERMES_HOME/.dashboard_session_token (0o600). Preserved across restarts
# to prevent spurious 401 re-auth pop-ups.
_DASHBOARD_TOKEN_FILE = ".dashboard_session_token"


def _get_effective_user_identity() -> tuple[Optional[str], Optional[str]]:
    """Return (user_sid, user_name) for the effective process token on Windows."""
    if os.name != "nt":
        return None, None
    # 1. Native Win32 token lookup via ctypes
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL

        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_uint,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

        advapi32.LookupAccountSidW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.LookupAccountSidW.restype = wintypes.BOOL

        TOKEN_QUERY = 0x0008
        TokenUser = 1

        hToken = wintypes.HANDLE()
        if advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(hToken)):
            try:
                cb = wintypes.DWORD()
                advapi32.GetTokenInformation(hToken, TokenUser, None, 0, ctypes.byref(cb))
                buf = ctypes.create_string_buffer(cb.value)
                if advapi32.GetTokenInformation(hToken, TokenUser, buf, cb.value, ctypes.byref(cb)):
                    sid_ptr = ctypes.c_void_p.from_buffer(buf)
                    pSidStr = ctypes.c_wchar_p()
                    if advapi32.ConvertSidToStringSidW(sid_ptr.value, ctypes.byref(pSidStr)):
                        sid = pSidStr.value
                        kernel32.LocalFree(pSidStr)
                        cchName = wintypes.DWORD(256)
                        cchDomain = wintypes.DWORD(256)
                        name_buf = ctypes.create_unicode_buffer(cchName.value)
                        domain_buf = ctypes.create_unicode_buffer(cchDomain.value)
                        snu = wintypes.DWORD()
                        account = None
                        if advapi32.LookupAccountSidW(
                            None,
                            sid_ptr.value,
                            name_buf,
                            ctypes.byref(cchName),
                            domain_buf,
                            ctypes.byref(cchDomain),
                            ctypes.byref(snu),
                        ):
                            account = f"{domain_buf.value}\\{name_buf.value}"
                        return sid, account
            finally:
                kernel32.CloseHandle(hToken)
    except Exception as exc:
        _log.debug("Native Win32 process token lookup failed: %s", exc)

    # 2. Native whoami /user fallback
    try:
        import subprocess

        res = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        parts = [p.strip().strip('"') for p in res.stdout.strip().split(",")]
        if len(parts) >= 2 and parts[1].startswith("S-1-"):
            return parts[1], parts[0]
    except Exception as exc:
        _log.debug("Native whoami /user fallback failed: %s", exc)

    # If verified native process token SID cannot be determined, fail closed.
    # Untrusted environment variables (e.g. USERNAME) must never be treated as authorization identity.
    _log.warning("Could not determine verified native Windows process token SID")
    return None, None


def _secure_token_file(path: Path) -> bool:
    """Ensure token file is accessible only to the owner (0o600 on Unix, allowlisted DACL on Windows)."""
    if not path.exists():
        return False
    from hermes_constants import secure_parent_dir
    secure_parent_dir(path)
    if os.name == "nt":
        try:
            import subprocess

            user_sid, user_name = _get_effective_user_identity()
            if not user_sid:
                _log.warning(
                    "Cannot secure token file %s: verified native process SID is required on Windows",
                    path,
                )
                return False

            user_grant = f"*{user_sid}:(F)"

            # Step 1: Remove inheritance and grant exclusive Full Control to user and SYSTEM
            res = subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    user_grant,
                    "/grant:r",
                    "*S-1-5-18:(F)",
                ],
                check=False,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode != 0:
                _log.warning(
                    "icacls failed setting ACL on %s (exit %d): %s",
                    path,
                    res.returncode,
                    res.stderr.decode("utf-8", errors="replace"),
                )
                return False

            # Step 2: Inspect DACL for any unauthorized principals and strip them
            chk = subprocess.run(
                ["icacls", str(path)],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if chk.returncode != 0:
                return False

            allowed_principals = {
                "s-1-5-18",
                "*s-1-5-18",
                "nt authority\\system",
                "system",
            }
            if user_sid:
                allowed_principals.add(user_sid.lower())
                allowed_principals.add(f"*{user_sid}".lower())
            if user_name:
                allowed_principals.add(user_name.lower())
                if "\\" in user_name:
                    allowed_principals.add(user_name.split("\\")[-1].lower())

            str_path = str(path)
            str_path_fwd = str_path.replace("\\", "/")
            str_path_back = str_path.replace("/", "\\")
            str_name = path.name

            for line in chk.stdout.splitlines():
                line = line.strip()
                if not line or "successfully processed" in line.lower() or "failed processing" in line.lower():
                    continue
                if ":(" in line:
                    before_colon = line.split(":(")[0].strip()
                    if before_colon.startswith(str_path):
                        principal = before_colon[len(str_path):].strip()
                    elif before_colon.startswith(str_path_fwd):
                        principal = before_colon[len(str_path_fwd):].strip()
                    elif before_colon.startswith(str_path_back):
                        principal = before_colon[len(str_path_back):].strip()
                    elif before_colon.startswith(str_name):
                        principal = before_colon[len(str_name):].strip()
                    else:
                        principal = before_colon

                    if principal.lower() not in allowed_principals:
                        subprocess.run(
                            ["icacls", str(path), "/remove", principal],
                            check=False,
                            capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        if principal.startswith("*"):
                            subprocess.run(
                                ["icacls", str(path), "/remove", principal.lstrip("*")],
                                check=False,
                                capture_output=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            )

            # Step 3: Strict verification — verify every remaining ACE is allowlisted
            verify = subprocess.run(
                ["icacls", str(path)],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if verify.returncode != 0:
                return False

            has_user = False
            for line in verify.stdout.splitlines():
                line = line.strip()
                if not line or "successfully processed" in line.lower() or "failed processing" in line.lower():
                    continue
                if ":(" in line:
                    before_colon = line.split(":(")[0].strip()
                    if before_colon.startswith(str_path):
                        principal = before_colon[len(str_path):].strip()
                    elif before_colon.startswith(str_path_fwd):
                        principal = before_colon[len(str_path_fwd):].strip()
                    elif before_colon.startswith(str_path_back):
                        principal = before_colon[len(str_path_back):].strip()
                    elif before_colon.startswith(str_name):
                        principal = before_colon[len(str_name):].strip()
                    else:
                        principal = before_colon

                    p_lower = principal.lower()
                    if p_lower not in allowed_principals:
                        _log.warning(
                            "Token file %s has unauthorized ACL principal %s; rejecting",
                            path,
                            principal,
                        )
                        return False
                    if (
                        (user_sid and p_lower in {user_sid.lower(), f"*{user_sid}".lower()})
                        or (user_name and p_lower in {user_name.lower(), user_name.split("\\")[-1].lower()})
                    ):
                        has_user = True

            return has_user
        except Exception as exc:
            _log.warning("Failed setting Windows ACL on %s: %s", path, exc)
            return False
    else:
        try:
            mode = path.stat().st_mode & 0o777
            if mode != 0o600:
                os.chmod(path, 0o600)
            return (path.stat().st_mode & 0o777) == 0o600
        except OSError as exc:
            _log.warning("Failed setting mode 0600 on %s: %s", path, exc)
            return False


def _publish_session_token(tmp_path: Path, token_path: Path) -> bool:
    """Atomically publish temporary token file to final token path (single-winner semantics)."""
    try:
        os.link(str(tmp_path), str(token_path))
        tmp_path.unlink(missing_ok=True)
        return True
    except FileExistsError:
        tmp_path.unlink(missing_ok=True)
        return False
    except OSError:
        if token_path.exists():
            tmp_path.unlink(missing_ok=True)
            return False
        try:
            os.replace(str(tmp_path), str(token_path))
            return True
        except OSError:
            tmp_path.unlink(missing_ok=True)
            return False


class _SessionTokenLock:
    """Cross-process whole-file lock serializing session token initialization/regeneration."""

    def __init__(self, lock_path: Path, timeout: float = 3.0, poll_interval: float = 0.05):
        self.lock_path = lock_path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fh = None
        self.acquired = False

    def __enter__(self):
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.lock_path, "a+b")
        except Exception as exc:
            _log.debug("Session token lock file open failed: %s", exc)
            self.acquired = False
            return self

        start = time.monotonic()
        while True:
            try:
                self._fh.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return self
            except OSError as exc:
                if time.monotonic() - start >= self.timeout:
                    _log.debug("Session token lock acquisition failed on %s: %s", self.lock_path, exc)
                    self.acquired = False
                    if self._fh is not None:
                        try:
                            self._fh.close()
                        except Exception:
                            pass
                        self._fh = None
                    return self
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc, tb):
        if self._fh is not None:
            if self.acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            self.acquired = False


def resolve_session_token() -> str:
    env_token = os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN")
    if env_token:
        # A "file:" prefix means the value is a token-file path, not a token:
        # deployment managers (agenix, sops-nix, systemd credentials) hand a
        # secret over as a file, and a path -- unlike the token -- can sit in a
        # systemd Environment= or a launchd plist without putting anything
        # readable into a store path.
        if env_token.startswith("file:"):
            token_file = env_token[len("file:") :]
            try:
                token = Path(token_file).read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError) as exc:
                raise SystemExit(
                    f"HERMES_DASHBOARD_SESSION_TOKEN names an unreadable token file ({token_file}): {exc}"
                ) from exc
            if not token:
                raise SystemExit(
                    f"HERMES_DASHBOARD_SESSION_TOKEN names an empty token file ({token_file})"
                )
            return token
        return env_token

    # Durable on-disk fallback in $HERMES_HOME/.dashboard_session_token
    from hermes_constants import get_hermes_home, secure_parent_dir

    try:
        from hermes_cli import web_server
        secure_fn = getattr(web_server, "_secure_token_file", _secure_token_file)
        lock_cls = getattr(web_server, "_SessionTokenLock", _SessionTokenLock)
        pub_fn = getattr(web_server, "_publish_session_token", _publish_session_token)
    except Exception:
        secure_fn = _secure_token_file
        lock_cls = _SessionTokenLock
        pub_fn = _publish_session_token

    token_path = get_hermes_home() / _DASHBOARD_TOKEN_FILE
    lock_path = token_path.with_name(f"{token_path.name}.lock")

    try:
        if token_path.is_file():
            if secure_fn(token_path):
                token = token_path.read_text(encoding="utf-8").strip()
                if token:
                    return token
            else:
                _log.warning(
                    "Session token file %s has insecure permissions that could not be repaired; rejecting",
                    token_path,
                )
                return secrets.token_urlsafe(32)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        _log.debug("Failed reading session token file %s: %s", token_path, exc)

    with lock_cls(lock_path) as lock:
        if not lock.acquired:
            # Under lock acquisition failure, we MUST NOT enter destructive regeneration
            # (C1-R2: lock ownership is a prerequisite for destructive regeneration/replacement).
            # Poll disk to see if the lock holder has published a valid token.
            for _ in range(10):
                try:
                    if token_path.is_file() and secure_fn(token_path):
                        persisted = token_path.read_text(encoding="utf-8").strip()
                        if persisted:
                            return persisted
                except (OSError, UnicodeDecodeError):
                    pass
                time.sleep(0.05)
            # Cannot safely regenerate without lock ownership; fail closed without touching disk
            _log.warning(
                "Lock acquisition failed for %s and no valid token on disk; failing closed without destructive regeneration",
                token_path,
            )
            return secrets.token_urlsafe(32)

        # Under lock, re-check if another caller/process generated it
        try:
            if token_path.is_file():
                if secure_fn(token_path):
                    token = token_path.read_text(encoding="utf-8").strip()
                    if token:
                        return token
                else:
                    _log.warning(
                        "Session token file %s has insecure permissions that could not be repaired; rejecting",
                        token_path,
                    )
                    return secrets.token_urlsafe(32)
        except (OSError, UnicodeDecodeError, ValueError):
            pass

        # If file existed but was corrupted or empty, remove it before writing fresh token
        if token_path.exists():
            with contextlib.suppress(OSError):
                token_path.unlink()

        token = secrets.token_urlsafe(32)
        tmp_path: Optional[Path] = None
        try:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            secure_parent_dir(token_path)
            tmp_path = token_path.with_name(f"{token_path.name}.tmp.{os.getpid()}.{secrets.token_hex(6)}")
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            if not secure_fn(tmp_path):
                _log.warning("Could not secure permissions on temporary token file %s; failing closed", tmp_path)
                tmp_path.unlink(missing_ok=True)
                return token
            tmp_path.write_text(token + "\n", encoding="utf-8")
            won = pub_fn(tmp_path, token_path)
            if won:
                secure_fn(token_path)
        except OSError as exc:
            _log.warning("Could not persist session token to %s: %s", token_path, exc)
        finally:
            if tmp_path is not None and tmp_path.exists():
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

        try:
            if token_path.is_file() and secure_fn(token_path):
                persisted = token_path.read_text(encoding="utf-8").strip()
                if persisted:
                    return persisted
        except (OSError, UnicodeDecodeError):
            pass

    # Re-read the final persisted token from disk so every concurrent caller
    # returns the exact same persisted credential.
    for _ in range(20):
        try:
            if token_path.is_file():
                if secure_fn(token_path):
                    persisted = token_path.read_text(encoding="utf-8").strip()
                    if persisted:
                        return persisted
        except (OSError, UnicodeDecodeError):
            pass
        time.sleep(0.01)
    return token


_resolve_session_token = resolve_session_token
