"""Tests for memory provider schema discovery under gateway multiplexing.

Regression test for UnscopedSecretError handling in hermes_cli/web_server_memory.py
when default profile secret scope is inactive.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from agent import secret_scope
from hermes_cli.web_server_memory import (
    _discover_memory_provider_statuses,
    _normalize_memory_provider_schema,
)


@pytest.fixture
def multiplex_inactive_scope(monkeypatch):
    """Enable multiplexing with no active secret scope."""
    secret_scope.set_multiplex_active(True)
    token = secret_scope.set_secret_scope(None)
    try:
        yield
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(False)


def test_normalize_schema_handles_unscoped_secret_error_safely(multiplex_inactive_scope, caplog):
    """When a provider's get_config_schema raises UnscopedSecretError, fallback is safe without warning tracebacks."""
    provider = MagicMock()

    def _mock_get_config_schema():
        # Call get_secret which raises UnscopedSecretError under multiplexing when scope is None
        secret_scope.get_secret("MEM0_MODE", "")
        return [
            {"key": "api_key", "secret": True, "required": True},
            {"key": "host", "required": False},
        ]

    provider.get_config_schema.side_effect = _mock_get_config_schema

    with caplog.at_level(logging.DEBUG):
        fields = _normalize_memory_provider_schema("mem0", provider)

    # 1. Fallback successfully populates fields
    keys = [f["key"] for f in fields]
    assert "api_key" in keys
    assert "host" in keys

    # 2. No WARNING records with exc_info (no tracebacks in errors.log)
    warning_tracebacks = [
        rec for rec in caplog.records
        if rec.levelno >= logging.WARNING and rec.exc_info
    ]
    assert not warning_tracebacks


def test_discover_memory_provider_statuses_safe_under_multiplex(multiplex_inactive_scope, caplog):
    """Full discovery runs without unhandled UnscopedSecretError or warning tracebacks."""
    with caplog.at_level(logging.DEBUG):
        statuses = _discover_memory_provider_statuses()

    assert isinstance(statuses, list)
    warning_tracebacks = [
        rec for rec in caplog.records
        if rec.levelno >= logging.WARNING and rec.exc_info and "mem0" in rec.getMessage()
    ]
    assert not warning_tracebacks
