"""Regression tests for Honcho asymmetric observer topology.

Covers:
- Bot-author join tiering (non-observing (False, False) joins for bot authors in shared sessions)
- Human-author join preservation (observing flags used for human authors)
- A2A isolation when a2aSessions is enabled
- Sequence interactions (bot-first then specialist-init, specialist-init then bot, etc.)
- Own-peer observation authority (reconciling server config when ai_authoritative=True)
- Join failure visibility (warning logs on add_peers and set_peer_configuration failures)
- Config parser resolution (host vs root authoritative resolution, default False)
- Divergence handling on rejected set_peer_configuration (server state retained, warning emitted)
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch
import pytest

from honcho import Honcho
from honcho.session import SessionPeerConfig
from plugins.memory.honcho.client import HonchoClientConfig
from plugins.memory.honcho.session import HonchoSession, HonchoSessionManager
from plugins.memory.honcho import HonchoMemoryProvider


class DummyPeer:
    def __init__(self, peer_id: str):
        self.id = peer_id
        self.name = peer_id

    def message(self, content: str):
        return {"peer_id": self.id, "content": content}

    def __repr__(self):
        return f"DummyPeer({self.id})"


class DummySession:
    def __init__(self, session_id: str):
        self.id = session_id
        self.added_peers: list[tuple[DummyPeer, SessionPeerConfig]] = []
        self.messages: list[dict] = []
        self.peer_configs: dict[str, SessionPeerConfig] = {}
        self.fail_add_peers: Exception | None = None
        self.fail_set_peer_config: Exception | None = None

    def add_peers(self, peer_entries):
        if self.fail_add_peers:
            raise self.fail_add_peers
        for peer, cfg in peer_entries:
            self.added_peers.append((peer, cfg))
            self.peer_configs[peer.id] = cfg

    def get_peer_configuration(self, peer):
        return self.peer_configs.get(peer.id, SessionPeerConfig(observe_me=True, observe_others=True))

    def set_peer_configuration(self, peer, config):
        if self.fail_set_peer_config:
            raise self.fail_set_peer_config
        self.peer_configs[peer.id] = config

    def add_messages(self, messages):
        self.messages.extend(messages)


_created_managers: list[HonchoSessionManager] = []


@pytest.fixture(autouse=True)
def _cleanup_topology_managers():
    yield
    while _created_managers:
        mgr = _created_managers.pop()
        try:
            mgr.shutdown()
        except Exception:
            pass


def _make_manager(config: HonchoClientConfig | None = None) -> tuple[HonchoSessionManager, MagicMock, dict[str, DummySession]]:
    mock_sdk = MagicMock(spec=Honcho)
    sessions: dict[str, DummySession] = {}

    def get_session(session_id: str):
        if session_id not in sessions:
            sessions[session_id] = DummySession(session_id)
        return sessions[session_id]

    mock_sdk.session.side_effect = get_session
    mock_sdk.peer.side_effect = lambda pid: DummyPeer(pid)

    cfg = config or HonchoClientConfig(
        host="test_host",
        ai_peer="hermes_agent",
        peer_name="user",
        api_key="test-key",
        write_frequency="turn",
    )
    if not cfg.api_key:
        cfg.api_key = "test-key"
    if not cfg.peer_name:
        cfg.peer_name = "user"
    mgr = HonchoSessionManager(
        honcho=mock_sdk,
        config=cfg,
    )
    mgr._sdk_session = lambda sid: sessions.setdefault(sid, DummySession(sid))
    mgr._get_or_create_peer = lambda pid: DummyPeer(pid)
    _created_managers.append(mgr)
    return mgr, mock_sdk, sessions


# ---------------------------------------------------------------------------
# Parser tests (Reviewer W-06 part 1)
# ---------------------------------------------------------------------------

def test_parser_authoritative_resolution(tmp_path):
    """Verify host vs root resolution of observation.ai.authoritative, defaulting to False."""
    def _load(raw: dict, host: str = "hermes"):
        cfg_file = tmp_path / f"honcho_{len(list(tmp_path.iterdir()))}.json"
        cfg_file.write_text(json.dumps(raw))
        return HonchoClientConfig.from_global_config(host=host, config_path=cfg_file)

    # 1. Default when absent
    cfg = _load({}, host="hermes")
    assert cfg.ai_authoritative is False

    # 2. Root level authoritative: true
    raw_root = {
        "observation": {"ai": {"authoritative": True}}
    }
    cfg_root = _load(raw_root, host="hermes")
    assert cfg_root.ai_authoritative is True

    # 3. Host level authoritative: true
    raw_host = {
        "hosts": {
            "coder": {
                "observation": {"ai": {"authoritative": True, "observeOthers": False}}
            }
        }
    }
    cfg_host = _load(raw_host, host="coder")
    assert cfg_host.ai_authoritative is True
    assert cfg_host.ai_observe_others is False

    # 4. Host overrides root (host=False, root=True)
    raw_override = {
        "observation": {"ai": {"authoritative": True}},
        "hosts": {
            "coder": {
                "observation": {"ai": {"authoritative": False}}
            }
        }
    }
    cfg_override = _load(raw_override, host="coder")
    assert cfg_override.ai_authoritative is False

    # 5. Host inherits root (host has observation without authoritative, root has True)
    raw_inherit = {
        "observation": {"ai": {"authoritative": True}},
        "hosts": {
            "coder": {
                "observation": {"ai": {"observeOthers": False}}
            }
        }
    }
    cfg_inherit = _load(raw_inherit, host="coder")
    assert cfg_inherit.ai_authoritative is True
    assert cfg_inherit.ai_observe_others is False

    # 6. Non-bool values rejected/normalised to False per design D2 rule 2
    raw_non_bool = {
        "observation": {"ai": {"authoritative": "true"}},
        "hosts": {
            "coder": {
                "observation": {"ai": {"authoritative": 1}}
            }
        }
    }
    cfg_non_bool_host = _load(raw_non_bool, host="coder")
    assert cfg_non_bool_host.ai_authoritative is False
    cfg_non_bool_root = _load(raw_non_bool, host="hermes")
    assert cfg_non_bool_root.ai_authoritative is False


# ---------------------------------------------------------------------------
# Scenario (a): raw bot:coder author in shared session -> non-observing join
# ---------------------------------------------------------------------------

def test_bot_author_shared_session_non_observing_join():
    """Bot author in shared session (a2a disabled) joins with (False, False) through real pipeline."""
    cfg = HonchoClientConfig(host="hermes", ai_peer="hermes_agent", a2a_sessions=False, write_frequency="turn")
    mgr, _, sessions = _make_manager(cfg)

    provider = HonchoMemoryProvider()
    provider._config = cfg
    provider._manager = mgr
    provider._session_key = "test_shared_a"
    provider._session_initialized = True

    # Real pipeline: on_turn_start -> sync_turn -> _flush_session_locked
    provider.on_turn_start(1, "Bot message", author_id="bot:coder", author_name="coder")
    provider.sync_turn(user_content="Bot message", assistant_content=None)
    if provider._sync_thread and provider._sync_thread.is_alive():
        provider._sync_thread.join(timeout=2.0)

    # Verify message has author_peer_id="coder" and author_is_bot=True
    cached_session = mgr._cache.get("test_shared_a")
    assert cached_session is not None
    user_msgs = [m for m in cached_session.messages if m.get("role") == "user"]
    assert len(user_msgs) >= 1
    assert user_msgs[0].get("author_peer_id") == "coder"
    assert user_msgs[0].get("author_is_bot") is True

    # Verify backend join: coder added as (False, False)
    sid = mgr._sanitize_id("test_shared_a")
    backend_session = sessions.get(sid)
    assert backend_session is not None
    coder_joins = [cfg for peer, cfg in backend_session.added_peers if peer.id == "coder"]
    assert len(coder_joins) == 1
    assert coder_joins[0].observe_me is False
    assert coder_joins[0].observe_others is False


# ---------------------------------------------------------------------------
# Scenario (b): human aliased author -> observing join
# ---------------------------------------------------------------------------

def test_human_aliased_author_observing_join():
    """Human/runtime author aliased to a peer joins with session user flags through real pipeline."""
    cfg = HonchoClientConfig(
        host="hermes",
        ai_peer="hermes_agent",
        user_peer_aliases={"runtime_human": "coder"},
        write_frequency="turn",
    )
    mgr, _, sessions = _make_manager(cfg)

    provider = HonchoMemoryProvider()
    provider._config = cfg
    provider._manager = mgr
    provider._session_key = "test_shared_b"
    provider._session_initialized = True

    # Real pipeline: human/runtime id aliased to peer coder, is_bot=False
    provider.on_turn_start(1, "Human message", author_id="runtime_human", author_name="coder", author_is_bot=False)
    provider.sync_turn(user_content="Human message", assistant_content=None)
    if provider._sync_thread and provider._sync_thread.is_alive():
        provider._sync_thread.join(timeout=2.0)

    cached_session = mgr._cache.get("test_shared_b")
    assert cached_session is not None
    user_msgs = [m for m in cached_session.messages if m.get("role") == "user"]
    assert len(user_msgs) >= 1
    assert user_msgs[0].get("author_peer_id") == "coder"
    assert user_msgs[0].get("author_is_bot") is False

    sid = mgr._sanitize_id("test_shared_b")
    backend_session = sessions.get(sid)
    assert backend_session is not None
    coder_joins = [c for peer, c in backend_session.added_peers if peer.id == "coder"]
    assert len(coder_joins) == 1
    flags = mgr._observation_flags(sid)
    assert coder_joins[0].observe_me == flags["user_observe_me"]
    assert coder_joins[0].observe_others == flags["user_observe_others"]


# ---------------------------------------------------------------------------
# Scenario (c): bot:<conn>/coder prefix classified as bot
# ---------------------------------------------------------------------------

def test_bot_prefix_connection_author_join():
    """Prefix bot: triggers author_is_bot in sync_turn and yields (False, False) join."""
    cfg = HonchoClientConfig(
        host="hermes",
        ai_peer="hermes_agent",
        peer_name="user",
        api_key="test-key",
        a2a_sessions=False,
        user_peer_aliases={"bot:conn/coder": "coder"},
        write_frequency="turn",
    )
    mgr, _, sessions = _make_manager(cfg)

    provider = HonchoMemoryProvider()
    provider._config = cfg
    provider._manager = mgr
    provider._session_key = "test_shared"
    provider._session_initialized = True

    # 1. bot:<conn>/specialist
    author = {"id": "bot:matrix/@specialist:homeserver", "name": "specialist"}
    provider.on_turn_start(1, "Hello from specialist", author_id=author["id"], author_name=author["name"])
    provider.sync_turn(user_content="Hello from specialist", assistant_content=None, turn_author=author)
    if provider._sync_thread and provider._sync_thread.is_alive():
        provider._sync_thread.join(timeout=2.0)

    # 2. peerAliases-mapped bot: id
    author_alias = {"id": "bot:conn/coder", "name": "coder"}
    provider.on_turn_start(2, "Hello from coder", author_id=author_alias["id"], author_name=author_alias["name"])
    provider.sync_turn(user_content="Hello from coder", assistant_content=None, turn_author=author_alias)
    if provider._sync_thread and provider._sync_thread.is_alive():
        provider._sync_thread.join(timeout=2.0)

    sid = mgr._sanitize_id("test_shared")
    backend_session = sessions.get(sid)
    assert backend_session is not None

    specialist_joins = [c for peer, c in backend_session.added_peers if "specialist" in peer.id]
    assert len(specialist_joins) == 1
    assert specialist_joins[0].observe_me is False
    assert specialist_joins[0].observe_others is False

    coder_joins = [c for peer, c in backend_session.added_peers if peer.id == "coder"]
    assert len(coder_joins) == 1
    assert coder_joins[0].observe_me is False
    assert coder_joins[0].observe_others is False


# ---------------------------------------------------------------------------
# Scenario (d): is_bot=true with a2aSessions=true routes to A2A session
# ---------------------------------------------------------------------------

def test_bot_a2a_session_no_shared_add_peers():
    """When a2aSessions is True, bot turn routes to dedicated A2A session without shared add_peers."""
    cfg = HonchoClientConfig(
        host="hermes",
        ai_peer="hermes_agent",
        peer_name="user",
        api_key="test-key",
        a2a_sessions=True,
        write_frequency="turn",
    )
    mgr, _, sessions = _make_manager(cfg)

    provider = HonchoMemoryProvider()
    provider._config = cfg
    provider._manager = mgr
    provider._session_key = "human_chat"
    provider._session_initialized = True

    # Pre-create the shared session
    mgr.get_or_create("human_chat")

    author = {"id": "bot:coder", "name": "coder", "is_bot": True}
    provider.on_turn_start(1, "Subtask done", author_id=author["id"], author_name=author["name"], author_is_bot=True)
    provider.sync_turn(user_content="Subtask done", assistant_content=None, turn_author=author)

    if provider._sync_thread and provider._sync_thread.is_alive():
        provider._sync_thread.join(timeout=2.0)

    # Shared session human_chat was not populated with add_peers for coder
    shared_sid = mgr._sanitize_id("human_chat")
    shared = sessions.get(shared_sid)
    if shared:
        coder_joins = [p for p, _ in shared.added_peers if p.id == "coder"]
        assert len(coder_joins) == 0


# ---------------------------------------------------------------------------
# Scenario (e): bot-author-first then specialist own-init
# ---------------------------------------------------------------------------

def test_bot_author_first_then_specialist_init():
    """Bot author joins with (False, False). Specialist later inits session with observeOthers=False.
    Configuration remains observeOthers=False."""
    # 1. Bot author joins session s3 through real pipeline
    cfg_bot = HonchoClientConfig(host="hermes", ai_peer="hermes_agent", a2a_sessions=False, write_frequency="turn")
    mgr, _, sessions = _make_manager(cfg_bot)

    provider = HonchoMemoryProvider()
    provider._config = cfg_bot
    provider._manager = mgr
    provider._session_key = "s3_key"
    provider._session_initialized = True

    author = {"id": "bot:coder", "name": "coder"}
    provider.on_turn_start(1, "Work log", author_id=author["id"], author_name=author["name"])
    provider.sync_turn(user_content="Work log", assistant_content=None, turn_author=author)
    if provider._sync_thread and provider._sync_thread.is_alive():
        provider._sync_thread.join(timeout=2.0)

    sid = mgr._sanitize_id("s3_key")
    dummy_s3 = sessions[sid]

    # coder is in s3 with (False, False)
    assert dummy_s3.peer_configs["coder"].observe_others is False

    # 2. Specialist own-init: manager configured with ai_peer="coder", ai_authoritative=True, ai_observe_others=False
    cfg_specialist = HonchoClientConfig(
        host="coder",
        ai_peer="coder",
        ai_authoritative=True,
        ai_observe_me=False,
        ai_observe_others=False,
        write_frequency="turn",
    )
    mgr_spec, _, _ = _make_manager(cfg_specialist)
    mgr_spec._sdk_session = lambda s: dummy_s3

    synced = mgr_spec._configure_session_peers(sid, DummyPeer("user"), DummyPeer("coder"))
    assert synced["ai_observe_others"] is False
    assert dummy_s3.peer_configs["coder"].observe_others is False


# ---------------------------------------------------------------------------
# Scenario (f): specialist-init-first then bot author -> no extra call
# ---------------------------------------------------------------------------

def test_specialist_init_first_then_bot_author():
    """Specialist inits session first; later bot write by coder sees coder already joined."""
    cfg = HonchoClientConfig(host="coder", ai_peer="coder", ai_authoritative=True, ai_observe_others=False)
    mgr, _, _ = _make_manager(cfg)
    dummy = DummySession("s4")
    mgr._sessions_cache["s4"] = dummy
    mgr._sdk_session = lambda sid: dummy

    # Specialist inits s4
    mgr._configure_session_peers("s4", DummyPeer("user"), DummyPeer("coder"))
    initial_add_count = len(dummy.added_peers)

    # Bot writes as coder
    session = HonchoSession(
        key="s4_key",
        user_peer_id="user",
        assistant_peer_id="coder",
        honcho_session_id="s4",
    )
    # Simulate coder already in _joined_author_peers or joined
    mgr._joined_author_peers.setdefault("s4", set()).add("coder")
    session.add_message("user", "Another task", author_peer_id="coder", author_is_bot=True)
    mgr._flush_session(session)

    # No additional add_peers for coder
    assert len(dummy.added_peers) == initial_add_count


# ---------------------------------------------------------------------------
# Scenario (g): bot-author-first then facilitator init with authoritative=true
# ---------------------------------------------------------------------------

def test_bot_author_first_then_facilitator_init_reconcile():
    """Bot author created peer with observeOthers=False. Facilitator (leader) inits with
    authoritative=true and observeOthers=True -> calls set_peer_configuration."""
    dummy = DummySession("s5")
    # Initial state on server: leader peer was joined earlier with observe_others=False
    dummy.peer_configs["leader"] = SessionPeerConfig(observe_me=False, observe_others=False)

    cfg = HonchoClientConfig(
        host="leader",
        ai_peer="leader",
        ai_authoritative=True,
        ai_observe_me=False,
        ai_observe_others=True,
    )
    mgr, _, _ = _make_manager(cfg)
    mgr._sdk_session = lambda sid: dummy

    synced = mgr._configure_session_peers("s5", DummyPeer("user"), DummyPeer("leader"))

    # Must have updated server config to observe_others=True
    assert dummy.peer_configs["leader"].observe_others is True
    assert synced["ai_observe_others"] is True


# ---------------------------------------------------------------------------
# Scenario (h): facilitator init with authoritative=false adopts server value
# ---------------------------------------------------------------------------

def test_facilitator_init_authoritative_false_adopts_server():
    """When authoritative=false, local defaults do not overwrite server toggles."""
    dummy = DummySession("s6")
    dummy.peer_configs["leader"] = SessionPeerConfig(observe_me=False, observe_others=False)

    cfg = HonchoClientConfig(
        host="leader",
        ai_peer="leader",
        ai_authoritative=False,
        ai_observe_me=False,
        ai_observe_others=True,
    )
    mgr, _, _ = _make_manager(cfg)
    mgr._sdk_session = lambda sid: dummy

    synced = mgr._configure_session_peers("s6", DummyPeer("user"), DummyPeer("leader"))

    # Server config retained (False), not updated to local True
    assert dummy.peer_configs["leader"].observe_others is False
    assert synced["ai_observe_others"] is False


# ---------------------------------------------------------------------------
# Scenario (i): add_peers raises observer-limit error -> WARNING, write persists
# ---------------------------------------------------------------------------

def test_bot_author_join_cap_rejection_logs_warning_persists_write(caplog):
    """When add_peers for author fails (e.g. cap limit), emit WARNING and persist message."""
    cfg = HonchoClientConfig(host="hermes", ai_peer="hermes_agent")
    mgr, _, _ = _make_manager(cfg)

    dummy = DummySession("s7")
    dummy.fail_add_peers = RuntimeError("observer quota exceeded (cap 10)")
    mgr._sessions_cache["s7"] = dummy

    session = HonchoSession(
        key="s7_key",
        user_peer_id="user",
        assistant_peer_id="hermes_agent",
        honcho_session_id="s7",
    )
    session.add_message("user", "Urgent note", author_peer_id="coder", author_is_bot=True)

    with caplog.at_level(logging.WARNING):
        mgr._flush_session(session)

    # Exactly one WARNING containing session id, peer id, and error
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Honcho author peer join failed" in r.message]
    assert len(warnings) == 1
    record = warnings[0]
    assert "s7" in record.message
    assert "coder" in record.message
    assert "observer quota exceeded" in record.message

    # Message write was still attempted and delivered to session
    assert len(dummy.messages) == 1
    assert dummy.messages[0]["peer_id"] == "coder"
    assert dummy.messages[0]["content"] == "Urgent note"


# ---------------------------------------------------------------------------
# Scenario (k) / Reviewer W-06 part 2: rejected set_peer_configuration
# ---------------------------------------------------------------------------

def test_set_peer_configuration_failure_logs_warning_retains_server_state(caplog):
    """When set_peer_configuration fails, emit exactly one WARNING and retain server state in synced."""
    dummy = DummySession("s8")
    # Server state is observe_others=True
    dummy.peer_configs["coder"] = SessionPeerConfig(observe_me=False, observe_others=True)
    dummy.fail_set_peer_config = RuntimeError("mutation rejected by server policy")

    # Local is authoritative and wants observe_others=False
    cfg = HonchoClientConfig(
        host="coder",
        ai_peer="coder",
        ai_authoritative=True,
        ai_observe_me=False,
        ai_observe_others=False,
    )
    mgr, _, _ = _make_manager(cfg)
    mgr._sdk_session = lambda sid: dummy

    with caplog.at_level(logging.WARNING):
        synced = mgr._configure_session_peers("s8", DummyPeer("user"), DummyPeer("coder"))

    # Exactly one WARNING record containing session id, peer id, and server error text
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Honcho set_peer_configuration failed" in r.message]
    assert len(warnings) == 1
    record = warnings[0]
    assert "s8" in record.message
    assert "coder" in record.message
    assert "mutation rejected by server policy" in record.message

    # Effective observation state must NOT falsely claim unapplied local flags;
    # it must retain the server's actual state (observe_others=True)
    assert synced["ai_observe_others"] is True
    assert dummy.peer_configs["coder"].observe_others is True
