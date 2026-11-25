"""Unit tests for Windows control pipe timeout defaults and bounded ERROR_PIPE_BUSY retry (B1)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from gateway import control_socket
from gateway import runtime_bootstrap_windows as win_boot


def test_default_client_timeout_platform_and_env_clamping(monkeypatch, caplog):
    monkeypatch.delenv("HERMES_CONTROL_PIPE_TIMEOUT", raising=False)
    assert control_socket._resolve_default_client_timeout(is_windows=True) == 5.0
    assert control_socket._resolve_default_client_timeout(is_windows=False) == 2.0

    monkeypatch.setenv("HERMES_CONTROL_PIPE_TIMEOUT", "7.5")
    assert control_socket._resolve_default_client_timeout(is_windows=True) == 7.5

    monkeypatch.setenv("HERMES_CONTROL_PIPE_TIMEOUT", "0.2")
    assert control_socket._resolve_default_client_timeout(is_windows=True) == 1.0

    monkeypatch.setenv("HERMES_CONTROL_PIPE_TIMEOUT", "99.0")
    assert control_socket._resolve_default_client_timeout(is_windows=True) == 30.0

    monkeypatch.setenv("HERMES_CONTROL_PIPE_TIMEOUT", "not-a-float")
    with caplog.at_level(logging.WARNING, logger="gateway.control_socket"):
        assert control_socket._resolve_default_client_timeout(is_windows=True) == 5.0
    assert any("HERMES_CONTROL_PIPE_TIMEOUT" in r.getMessage() for r in caplog.records)


def test_query_gateway_control_uses_windows_5s_default_and_honors_explicit_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_CONTROL_PIPE_TIMEOUT", raising=False)
    monkeypatch.setattr(control_socket, "_IS_WINDOWS", True)
    seen_timeouts: list[float] = []

    def fake_windows_pipe(home: Path, request: bytes, timeout: float):
        seen_timeouts.append(timeout)
        return json.dumps({"ok": True, "protocol": 1, "result": {"pid": 123}}).encode("utf-8") + b"\n"

    monkeypatch.setattr(control_socket, "_query_windows_pipe", fake_windows_pipe)

    res_default = control_socket.query_gateway_control(tmp_path, "identify")
    res_explicit = control_socket.query_gateway_control(tmp_path, "identify", timeout=8.0)

    assert res_default == {"pid": 123}
    assert res_explicit == {"pid": 123}
    assert seen_timeouts == [5.0, 8.0]


def test_windows_pipe_exchange_retries_once_on_error_pipe_busy_within_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(control_socket, "_IS_WINDOWS", True)
    attempts: list[float] = []
    clock = [10.0]

    monkeypatch.setattr(control_socket.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(control_socket.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    def fake_query_runtime_control(home: Path, request: bytes, timeout: float) -> bytes:
        attempts.append(timeout)
        clock[0] += 0.2
        if len(attempts) == 1:
            err = OSError("All pipe instances are busy")
            err.winerror = 231
            raise err
        return json.dumps({"ok": True, "protocol": 1, "result": {"status": "ok"}}).encode("utf-8") + b"\n"

    monkeypatch.setattr(win_boot, "query_runtime_control", fake_query_runtime_control)

    result = control_socket.query_gateway_control(tmp_path, "status", timeout=2.0)
    assert result == {"status": "ok"}
    assert len(attempts) == 2
    assert attempts[0] == pytest.approx(2.0)
    # Second attempt receives strictly the remaining deadline (< 2.0s and > 0.0s)
    assert 0.0 < attempts[1] < 1.8
    # Total elapsed time never exceeded the 2.0s budget
    assert (clock[0] - 10.0) <= 2.0


def test_windows_pipe_exchange_aborts_without_retry_when_deadline_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(control_socket, "_IS_WINDOWS", True)
    attempts: list[float] = []
    clock = [50.0]

    monkeypatch.setattr(control_socket.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(control_socket.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    def fake_query_runtime_control(home: Path, request: bytes, timeout: float) -> bytes:
        attempts.append(timeout)
        clock[0] += 2.1  # Exhausts the 2.0s deadline
        err = OSError("All pipe instances are busy")
        err.winerror = 231
        raise err

    monkeypatch.setattr(win_boot, "query_runtime_control", fake_query_runtime_control)

    result = control_socket.query_gateway_control(tmp_path, "identify", timeout=2.0)
    assert result is None
    assert len(attempts) == 1
