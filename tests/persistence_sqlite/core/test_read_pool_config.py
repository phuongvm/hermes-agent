"""Unit tests for configurable SQLite WAL read-pool ceilings and FD headroom gating (B3)."""

from __future__ import annotations

import logging

import pytest

from persistence.sqlite._holder import StoreHolder
from persistence.sqlite._state import readpool


def test_default_read_pool_ceilings_are_16_per_file_and_48_per_process(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_SQLITE_READ_POOL_MAX", raising=False)
    monkeypatch.delenv("HERMES_SQLITE_READ_POOL_PROCESS_MAX", raising=False)
    readpool._reconfigure_read_pool_limits()
    try:
        assert readpool._READ_POOL_MAX == 16
        assert readpool._READ_POOL_PROCESS_MAX == 48
        holder = StoreHolder(tmp_path / "state.db")
        try:
            assert holder._read_budget.max_permits == 16
            assert holder._read_pool.maxsize == 16
        finally:
            holder.close()
    finally:
        readpool._reconfigure_read_pool_limits()


def test_env_overrides_clamp_and_enforce_process_ge_per_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SQLITE_READ_POOL_MAX", "20")
    monkeypatch.setenv("HERMES_SQLITE_READ_POOL_PROCESS_MAX", "12")
    readpool._reconfigure_read_pool_limits()
    try:
        assert readpool._READ_POOL_MAX == 20
        assert readpool._READ_POOL_PROCESS_MAX == 20
        holder = StoreHolder(tmp_path / "env_state.db")
        try:
            assert holder._read_budget.max_permits == 20
            assert holder._read_pool.maxsize == 20
        finally:
            holder.close()

        # Clamping bounds: per-file [1, 64], process [per_file, 256]
        monkeypatch.setenv("HERMES_SQLITE_READ_POOL_MAX", "999")
        monkeypatch.setenv("HERMES_SQLITE_READ_POOL_PROCESS_MAX", "999")
        readpool._reconfigure_read_pool_limits()
        assert readpool._READ_POOL_MAX == 64
        assert readpool._READ_POOL_PROCESS_MAX == 256

        monkeypatch.setenv("HERMES_SQLITE_READ_POOL_MAX", "0")
        monkeypatch.setenv("HERMES_SQLITE_READ_POOL_PROCESS_MAX", "-5")
        readpool._reconfigure_read_pool_limits()
        assert readpool._READ_POOL_MAX == 1
        assert readpool._READ_POOL_PROCESS_MAX == 1
    finally:
        monkeypatch.delenv("HERMES_SQLITE_READ_POOL_MAX", raising=False)
        monkeypatch.delenv("HERMES_SQLITE_READ_POOL_PROCESS_MAX", raising=False)
        readpool._reconfigure_read_pool_limits()


def test_invalid_env_values_warn_and_fallback_preserving_exhaustion_log(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HERMES_SQLITE_READ_POOL_MAX", "notanumber")
    monkeypatch.setenv("HERMES_SQLITE_READ_POOL_PROCESS_MAX", "badproc")
    readpool._pool_exhaustion_warned_at.clear()
    try:
        with caplog.at_level(logging.WARNING, logger="hermes_state"):
            readpool._reconfigure_read_pool_limits()
            assert readpool._READ_POOL_MAX == 16
            assert readpool._READ_POOL_PROCESS_MAX == 48
            readpool.note_read_pool_exhausted(tmp_path / "warn.db")

        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("HERMES_SQLITE_READ_POOL_MAX" in m for m in messages)
        assert any("read pool at capacity (16)" in m for m in messages)
    finally:
        monkeypatch.delenv("HERMES_SQLITE_READ_POOL_MAX", raising=False)
        monkeypatch.delenv("HERMES_SQLITE_READ_POOL_PROCESS_MAX", raising=False)
        readpool._reconfigure_read_pool_limits()


def test_posix_fd_headroom_guard_remains_enforced_at_64(tmp_path, monkeypatch):
    assert readpool._FD_HEADROOM_RESERVE == 64
    monkeypatch.setattr(readpool, "_fd_soft_limit", lambda: 256)
    monkeypatch.setattr(readpool, "_open_fd_count", lambda: 192)  # 256 - 192 == 64 <= 64 -> False
    readpool._fd_usage_cache = (0.0, None)

    assert readpool._fd_headroom_ok() is False

    holder = StoreHolder(tmp_path / "fd_guard.db")
    try:
        monkeypatch.setattr(holder, "_wal_active", True)
        readpool._fd_usage_cache = (0.0, None)
        assert holder._get_read_conn() is None
    finally:
        holder.close()
