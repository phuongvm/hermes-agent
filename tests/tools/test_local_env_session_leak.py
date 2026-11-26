"""Cross-session HERMES_SESSION_* leak guard for the local terminal backend.

Regression coverage for the bug where a terminal subprocess could observe a
*different concurrent session's* ``HERMES_SESSION_KEY`` (and the other
``HERMES_SESSION_*`` vars).

Root cause: the session vars have a process-global ``os.environ`` mirror (written
last-writer-wins as a CLI/cron fallback, never cleared), while the
concurrency-safe source of truth is a task-local ``ContextVar``. The subprocess
env was built from ``os.environ`` and only *overrode* the session vars when the
ContextVar was set+truthy. When the subprocess was spawned from a thread/context
that never inherited the agent's copied context (ContextVar ``_UNSET``), the
override no-op'd and the stale, foreign ``os.environ`` value leaked into the
child — so e.g. ``bug_thread.py whoami`` read another session's thread id.

The fix: once the session-context machinery is engaged in this process (any
concurrent host — gateway, ACP, API server, TUI, cron — has called
``set_session_vars``), the session vars are ContextVar-authoritative. The
subprocess-env bridge resolves each ``HERMES_SESSION_*`` from the ContextVar and,
when it is ``_UNSET``, STRIPS the var from the child env rather than inheriting
the process-global value that may belong to another session. A pure
single-process CLI/one-shot that never engaged the session-context system keeps
the ``os.environ`` fallback.
"""

import os
from pathlib import Path

import pytest

import gateway.session_context as sc
from gateway.session_context import _VAR_MAP, clear_session_vars, set_session_vars
from tools.environments.local import _make_run_env, _sanitize_subprocess_env, hermes_subprocess_env

# The full set of session vars the bridge owns.
SESSION_VARS = list(_VAR_MAP.keys())


@pytest.fixture(autouse=True)
def _isolate_session_context():
    """Clean ContextVar + os.environ + engaged-latch slate per test, restored."""
    saved_env = {k: os.environ.get(k) for k in SESSION_VARS}
    saved_ctx = {name: var.get() for name, var in _VAR_MAP.items()}
    saved_engaged = sc._session_context_engaged
    for var in _VAR_MAP.values():
        var.set(sc._UNSET)
    sc._session_context_engaged = False
    try:
        yield
    finally:
        for var, val in zip(_VAR_MAP.values(), saved_ctx.values()):
            var.set(val)
        sc._session_context_engaged = saved_engaged
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _engage():
    """Mark the session-context machinery engaged, like a concurrent host would."""
    sc._session_context_engaged = True


# --------------------------------------------------------------------------- #
# Foreground path (_make_run_env)
# --------------------------------------------------------------------------- #

def test_engaged_unset_contextvar_strips_foreign_session_key(monkeypatch):
    """Engaged host + UNSET ContextVar must NOT inherit a foreign global.

    This is the production hijack: a concurrent session wrote
    os.environ["HERMES_SESSION_KEY"], this task's ContextVar is unset, and the
    subprocess must see NO key rather than the foreign one.
    """
    _engage()
    monkeypatch.setenv(
        "HERMES_SESSION_KEY",
        "agent:main:discord:thread:FOREIGN_CONCURRENT:FOREIGN_CONCURRENT",
    )

    env = _make_run_env({})

    assert "HERMES_SESSION_KEY" not in env, (
        "Foreign concurrent session key leaked into subprocess env: "
        f"{env.get('HERMES_SESSION_KEY')!r}"
    )


def test_set_session_vars_engages_and_overrides_foreign_global(monkeypatch):
    """set_session_vars itself engages the latch and the bound value wins.

    Mirrors a real host: calling set_session_vars both marks the process engaged
    and binds the ContextVar, so the bound value overrides the foreign global.
    """
    monkeypatch.setenv(
        "HERMES_SESSION_KEY",
        "agent:main:discord:thread:FOREIGN:FOREIGN",
    )

    tokens = set_session_vars(
        session_key="agent:main:discord:group:MY_BUGS_ROOT:111",
        platform="discord",
        chat_id="MY_BUGS_ROOT",
    )
    try:
        assert sc.session_context_engaged() is True
        env = _make_run_env({})
    finally:
        clear_session_vars(tokens)

    assert env.get("HERMES_SESSION_KEY") == "agent:main:discord:group:MY_BUGS_ROOT:111"


def test_unengaged_process_preserves_os_environ_fallback(monkeypatch):
    """A process that never engaged the session-context system keeps the fallback.

    Pure single-process CLI/one-shot sets HERMES_SESSION_* directly in os.environ
    and relies on the subprocess inheriting them; there is no concurrency to leak
    across, so the strip must NOT apply.
    """
    # _isolate_session_context already forced engaged=False.
    monkeypatch.setenv("HERMES_SESSION_KEY", "cli-session-key")
    monkeypatch.setenv("HERMES_SESSION_ID", "cli-session-id")

    env = _make_run_env({})

    assert env.get("HERMES_SESSION_KEY") == "cli-session-key"
    assert env.get("HERMES_SESSION_ID") == "cli-session-id"


def test_explicit_empty_thread_id_overrides_stale_value(monkeypatch):
    """A bound-but-empty thread id must override a stale inherited value.

    This is the complementary case (the #38507 scenario): a top-level post with
    no thread id binds HERMES_SESSION_THREAD_ID="" and that empty value must win
    over an older non-empty value left in os.environ.
    """
    monkeypatch.setenv("HERMES_SESSION_THREAD_ID", "stale-thread-from-prior-turn")

    tokens = set_session_vars(
        session_key="mm:chan",
        platform="mattermost",
        chat_id="chan",
        thread_id="",  # explicitly no thread
    )
    try:
        env = _make_run_env({})
    finally:
        clear_session_vars(tokens)

    assert env.get("HERMES_SESSION_THREAD_ID") == "", (
        "Bound-empty thread id did not override the stale value: "
        f"{env.get('HERMES_SESSION_THREAD_ID')!r}"
    )
    assert env.get("HERMES_SESSION_KEY") == "mm:chan"


# --------------------------------------------------------------------------- #
# Background / PTY path (_sanitize_subprocess_env via process_registry)
# --------------------------------------------------------------------------- #

def test_sanitize_subprocess_env_strips_foreign_session_key_when_engaged(monkeypatch):
    """The background/PTY spawn path gets the same cross-session strip.

    process_registry.spawn_local() builds its env via _sanitize_subprocess_env(
    os.environ, env_vars). A background subprocess spawned with an UNSET
    ContextVar in an engaged process must not inherit a foreign session key.
    """
    _engage()
    stale_base = {
        "PATH": "/usr/bin:/bin",
        "HERMES_SESSION_KEY": "agent:main:discord:thread:FOREIGN_BG:FOREIGN_BG",
        "HERMES_SESSION_THREAD_ID": "FOREIGN_BG",
    }

    sanitized = _sanitize_subprocess_env(stale_base)

    assert "HERMES_SESSION_KEY" not in sanitized, (
        f"Background subprocess inherited foreign key: {sanitized.get('HERMES_SESSION_KEY')!r}"
    )
    assert "HERMES_SESSION_THREAD_ID" not in sanitized


def test_sanitize_subprocess_env_set_contextvar_wins_when_engaged():
    """Background path: a SET ContextVar overrides the foreign global base."""
    stale_base = {
        "PATH": "/usr/bin:/bin",
        "HERMES_SESSION_KEY": "agent:main:discord:thread:FOREIGN_BG:FOREIGN_BG",
    }
    tokens = set_session_vars(
        session_key="agent:main:discord:group:REAL_BG:222",
        platform="discord",
        chat_id="REAL_BG",
    )
    try:
        sanitized = _sanitize_subprocess_env(stale_base)
    finally:
        clear_session_vars(tokens)

    assert sanitized.get("HERMES_SESSION_KEY") == "agent:main:discord:group:REAL_BG:222"


# --------------------------------------------------------------------------- #
# Non-terminal spawn surface (hermes_subprocess_env) — sibling path
# --------------------------------------------------------------------------- #

def test_hermes_subprocess_env_strips_foreign_session_key_when_engaged(monkeypatch):
    """hermes_subprocess_env (browser/ACP/CLI/TUI-host spawns) must not leak a
    foreign session key either. cli.exec spawns via this helper WITHOUT re-binding
    the session identity, so an UNSET ContextVar under an engaged host must strip
    the inherited global rather than hand the child another session's identity.
    """
    _engage()
    monkeypatch.setenv(
        "HERMES_SESSION_KEY",
        "agent:main:discord:thread:FOREIGN_CONCURRENT:FOREIGN_CONCURRENT",
    )

    env = hermes_subprocess_env()

    assert "HERMES_SESSION_KEY" not in env, (
        "Foreign concurrent session key leaked into non-terminal spawn env: "
        f"{env.get('HERMES_SESSION_KEY')!r}"
    )


def test_hermes_subprocess_env_unengaged_preserves_fallback(monkeypatch):
    """A pure single-process CLI (never engaged) keeps the inherited fallback."""
    monkeypatch.setenv("HERMES_SESSION_KEY", "cli-fallback-key")
    # not engaged (autouse fixture leaves _session_context_engaged False)
    env = hermes_subprocess_env()
    assert env.get("HERMES_SESSION_KEY") == "cli-fallback-key"


# --------------------------------------------------------------------------- #
# Turn lease holder ContextVar seam (HERMES_SESSION_TURN_LEASE_HOLDER)
# --------------------------------------------------------------------------- #

def test_scoped_turn_lease_holder_propagates_to_child_envs_and_restores_on_exit():
    """scoped_turn_lease_holder binds HERMES_SESSION_TURN_LEASE_HOLDER in _VAR_MAP without
    mutating os.environ or shifting set_session_vars positional args, and restores on exit/error."""
    import re
    from gateway.session_context import reset_session_vars, scoped_turn_lease_holder
    from tools.environments.base_session_env import (
        _SNAPSHOT_EXCLUDED_ENV_REGEX,
        _export_dump_excluding_session_vars,
    )
    from tools.environments.local import build_subprocess_env

    assert Path(sc.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[2])
    holder = "pid=31596:turn=local-abc:cron:job1:exec1:deadbeef:platform=cron"
    assert "HERMES_SESSION_TURN_LEASE_HOLDER" in _VAR_MAP
    assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in os.environ

    # Verify set_session_vars positional tuple mapping is untouched (actor_id/actor_name intact)
    tokens = set_session_vars(
        platform="cron",
        session_id="local-abc",
        user_id="route-u",
        actor_id="actor-u",
        actor_name="actor-n",
    )
    try:
        assert sc.get_session_env("HERMES_SESSION_ACTOR_ID") == "actor-u"
        assert sc.get_session_env("HERMES_SESSION_ACTOR_NAME") == "actor-n"
        with scoped_turn_lease_holder(holder):
            assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in os.environ
            assert _make_run_env({}).get("HERMES_SESSION_TURN_LEASE_HOLDER") == holder
            assert build_subprocess_env(scrub_secrets=True).get("HERMES_SESSION_TURN_LEASE_HOLDER") == holder
            assert _sanitize_subprocess_env({"PATH": "/usr/bin"}).get("HERMES_SESSION_TURN_LEASE_HOLDER") == holder
            assert hermes_subprocess_env().get("HERMES_SESSION_TURN_LEASE_HOLDER") == holder

            # Nested None masks outer holder inside unleased child turn, then restores outer holder
            with scoped_turn_lease_holder(None):
                assert _make_run_env({}).get("HERMES_SESSION_TURN_LEASE_HOLDER") == ""
            assert _make_run_env({}).get("HERMES_SESSION_TURN_LEASE_HOLDER") == holder

        # Restored to _UNSET on normal exit (stripped because host is engaged)
        assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in _make_run_env({})

        # Restored on exception too
        with pytest.raises(RuntimeError, match="boom"):
            with scoped_turn_lease_holder(holder):
                assert _make_run_env({}).get("HERMES_SESSION_TURN_LEASE_HOLDER") == holder
                raise RuntimeError("boom")
        assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in _make_run_env({})
    finally:
        clear_session_vars(tokens)

    # clear_session_vars sets explicit ""; reset_session_vars restores _UNSET
    assert sc.bound_session_env("HERMES_SESSION_TURN_LEASE_HOLDER") == ""
    reset_session_vars()
    assert sc._VAR_MAP["HERMES_SESSION_TURN_LEASE_HOLDER"].get() is sc._UNSET

    # Excluded from shared bash session snapshots
    decl_line = f'declare -x HERMES_SESSION_TURN_LEASE_HOLDER="{holder}"'
    assert re.search(_SNAPSHOT_EXCLUDED_ENV_REGEX, decl_line) is not None
    dump_script = _export_dump_excluding_session_vars('"$__hermes_snap_tmp"')
    assert "${!HERMES_SESSION_*}" in dump_script


def test_engaged_unset_strips_foreign_turn_lease_holder(monkeypatch):
    """Engaged multi-session host strips foreign os.environ[HERMES_SESSION_TURN_LEASE_HOLDER] when _UNSET."""
    from tools.environments.local import build_subprocess_env

    _engage()
    monkeypatch.setenv(
        "HERMES_SESSION_TURN_LEASE_HOLDER",
        "pid=99999:turn=foreign:cron:jobX:execX:0000:platform=cron",
    )

    assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in _make_run_env({})
    assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in build_subprocess_env(scrub_secrets=True)
    assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in _sanitize_subprocess_env(dict(os.environ))
    assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in hermes_subprocess_env()


def test_turn_facade_run_conversation_binds_and_resets_turn_lease_holder(monkeypatch):
    """TurnFacadeMixin.run_conversation binds lease.holder for the turn duration and resets on exit/error."""
    import threading
    from agent.turn_facade import TurnFacadeMixin
    from tools.environments.local import build_subprocess_env

    monkeypatch.setattr("agent.turn_liveness.resolve_turn_liveness_settings", lambda cfg: (None, 1.0))

    class _LeaseDb:
        def __init__(self):
            self.holders = []

        def get_session(self, session_id):
            return {"id": session_id}

        def acquire_session_turn_lease(self, session_id, holder, **kwargs):
            self.holders.append(holder)
            return True

        def refresh_session_turn_lease(self, session_id, holder, **kwargs):
            return True

        def release_session_turn_lease(self, session_id, holder):
            pass

    class _DummyAgent(TurnFacadeMixin):
        def __init__(self, db):
            self._session_db = db
            self.session_id = "local-cron-sess"
            self.platform = "cron"
            self.model = "test-model"
            self.provider = "test-provider"
            self._persist_disabled = False
            self._interrupt_requested = False
            self._interrupt_message = None
            self._execution_thread_id = None
            self._session_turn_lease_refresh_interval = 60.0
            self._lock = threading.Lock()

        def _conversation_root_id(self):
            return self.session_id

        def _emit_status(self, msg):
            pass

        def _emit_warning(self, msg):
            pass

        def _touch_activity(self, *args, **kwargs):
            pass

        def _liveness_activity_lock(self):
            return self._lock

        def _reset_activity_labels_after_turn(self):
            pass

    _engage()
    db = _LeaseDb()
    agent = _DummyAgent(db)
    observed = {}

    def _fake_loop(self_agent, user_message, *args, **kwargs):
        observed["run_env"] = _make_run_env({}).get("HERMES_SESSION_TURN_LEASE_HOLDER")
        observed["subproc_env"] = build_subprocess_env(scrub_secrets=True).get("HERMES_SESSION_TURN_LEASE_HOLDER")
        if user_message == "raise":
            raise ValueError("turn boom")
        return {"final_response": "ok", "messages": []}

    monkeypatch.setattr("agent.conversation_loop.run_conversation", _fake_loop)

    res = agent.run_conversation("hello", task_id="cron:job1:exec1")
    assert res["final_response"] == "ok"
    assert len(db.holders) == 1
    expected_holder = db.holders[0]
    assert expected_holder.endswith(":platform=cron")
    assert observed["run_env"] == expected_holder
    assert observed["subproc_env"] == expected_holder
    assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in _make_run_env({})

    with pytest.raises(ValueError, match="turn boom"):
        agent.run_conversation("raise", task_id="cron:job1:exec2")
    assert observed["run_env"] == db.holders[1]
    assert "HERMES_SESSION_TURN_LEASE_HOLDER" not in _make_run_env({})
