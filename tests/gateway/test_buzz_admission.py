"""P0 Buzz admission (hermes-security-hardening-p0-p1, task 0.2): the live allowlist admits one pubkey, denies the rest.

Pinned to the exact values applied to the agent4070 config: ``allow_all_users: false`` and a single allowed pubkey. The
gateway authz layer is exercised through ``_is_user_authorized`` (the same seam as ``test_buzz_authz.py``); the adapter's
own pre-filter is checked through the ``_allowed_pubkeys`` set it builds from ``extra.allowed_users``.
"""
import pytest

from tests.gateway import test_buzz_authz as authz

# Fixtures are re-exported as module attributes (pytest collects them by name); plain imports would be
# shadowed by the test parameters of the same name (F811).
buzz_registered = authz.buzz_registered
_make_runner_with_adapter = authz._make_runner_with_adapter
_make_source = authz._make_source


@pytest.fixture(autouse=True)
def _clean_buzz_auth_env(monkeypatch):
    for var in authz._AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


ALLOWED = "6eeda0568fee98409dc571d2d809dff0a92772fa42fe69171e8a859bb9c65a45"
STRANGER = "b" * 64
LIVE_EXTRA = {"allowed_users": [ALLOWED], "allow_all_users": False}


def test_allowlisted_pubkey_is_authorized(buzz_registered):
    assert _make_runner_with_adapter(LIVE_EXTRA)._is_user_authorized(_make_source(ALLOWED)) is True


@pytest.mark.parametrize("sender", [STRANGER, "a" * 64, ALLOWED[:-1] + "0", ALLOWED.upper()[:-1] + "0"])
def test_every_other_pubkey_is_denied(buzz_registered, sender):
    assert _make_runner_with_adapter(LIVE_EXTRA)._is_user_authorized(_make_source(sender)) is False


def test_env_without_allow_all_does_not_reopen_access(buzz_registered, monkeypatch):
    monkeypatch.setenv("BUZZ_ALLOWED_USERS", ALLOWED)  # the live .env value (equal to config)
    runner = _make_runner_with_adapter(LIVE_EXTRA)
    assert runner._is_user_authorized(_make_source(ALLOWED)) is True
    assert runner._is_user_authorized(_make_source(STRANGER)) is False


def test_adapter_prefilter_holds_exactly_the_one_pubkey():
    from plugins.platforms.buzz.adapter import _pubkey_set

    assert _pubkey_set(LIVE_EXTRA["allowed_users"]) == {ALLOWED}
