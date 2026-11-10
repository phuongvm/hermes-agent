"""Tests for declarative fleet configuration overlay (config.fleet.yaml) and MCP availability mask.

Comprehensive coverage for Task L-1:
- SUPPORTS_FLEET_OVERLAY flag availability
- get_fleet_config_path() path resolution
- Layer 1 baseline merge from config.fleet.yaml over config.yaml (default_enabled)
- Stripping of forbidden fleet-owned enabled and mcp_overrides (Warning A-04)
- Full Truth Table coverage (Rules E-1 through E-5):
  - Rule E-1a / E-1b: Operator explicit false ALWAYS dominates
  - Rule E-2a: Operator explicit true wins when binary available
  - Rule E-2b: Missing binary overrides operator true
  - Rule E-3: Operator unset uses fleet default_enabled
  - Rule E-4a / E-4b: Missing binary disables server when operator unset
  - Rule E-5: Conflict between mcp_overrides and mcp_servers: mcp_overrides dominates
- Object-schema mask normalization (Critical A-01: dict entries with server_id)
- Cache signature invalidation on SEALED pointer, base mask, fleet config, and active mask (Critical A-02)
- Corrupt JSON active mask graceful fallback to base mask
- Exact byte-preservation of config.yaml comments across fleet load (Warning A-05)
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from hermes_cli.config import (
    SUPPORTS_FLEET_OVERLAY,
    _load_config_cache_sig,
    get_config_path,
    get_fleet_config_path,
    load_config,
)


def test_supports_fleet_overlay_flag():
    """SUPPORTS_FLEET_OVERLAY must be exported and True at module level."""
    assert SUPPORTS_FLEET_OVERLAY is True


def test_get_fleet_config_path(tmp_path, monkeypatch):
    """get_fleet_config_path() resolves to <HERMES_HOME>/config.fleet.yaml."""
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert get_fleet_config_path() == home / "config.fleet.yaml"


def test_fleet_overlay_baseline_merge(tmp_path, monkeypatch):
    """Layer 1 settings in config.fleet.yaml merge into effective configuration."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # User config
    user_cfg = {
        "model": {"default": "cb-custom-model"},
        "agent": {"max_turns": 45},
    }
    (home / "config.yaml").write_text(yaml.safe_dump(user_cfg), encoding="utf-8")

    # Fleet overlay with canonical default_enabled (Warning A-04)
    fleet_cfg = {
        "mcp_servers": {
            "fetch": {
                "command": "uvx",
                "args": ["mcp-server-fetch"],
                "default_enabled": True,
            }
        },
        "terminal": {"timeout": 300},
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    assert loaded["model"]["default"] == "cb-custom-model"
    assert loaded["agent"]["max_turns"] == 45
    assert loaded["terminal"]["timeout"] == 300
    assert "fetch" in loaded["mcp_servers"]
    assert loaded["mcp_servers"]["fetch"]["command"] == "uvx"
    assert loaded["mcp_servers"]["fetch"]["enabled"] is True


def test_fleet_overlay_strips_forbidden_enabled_and_mcp_overrides(tmp_path, monkeypatch):
    """Warning A-04: Fleet overlay must not be allowed to own enabled or mcp_overrides."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    fleet_cfg = {
        "mcp_servers": {
            "test_server": {
                "command": "npx",
                "enabled": True,  # Forbidden fleet key
            }
        },
        "mcp_overrides": {  # Forbidden fleet block
            "test_server": {"enabled": True}
        }
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    # Fleet mcp_overrides must be stripped from effective config
    assert "mcp_overrides" not in loaded or "test_server" not in loaded.get("mcp_overrides", {})
    # default_enabled was synthesized from enabled
    assert loaded["mcp_servers"]["test_server"]["enabled"] is True


def test_rule_e1_operator_explicit_false_dominance(tmp_path, monkeypatch):
    """Rule E-1a / E-1b: Operator explicit false ALWAYS dominates regardless of mask or fleet default."""
    home = tmp_path / ".hermes"
    fleet_state = home / "state" / "fleet"
    fleet_state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Mask disables server2
    (fleet_state / "mcp_availability.json").write_text(
        json.dumps({"disabled_servers": ["server2"]}),
        encoding="utf-8",
    )

    # Operator explicitly disables both server1 and server2
    user_cfg = {
        "mcp_servers": {
            "server1": {"enabled": False},
        },
        "mcp_overrides": {
            "server2": {"enabled": False},
        }
    }
    (home / "config.yaml").write_text(yaml.safe_dump(user_cfg), encoding="utf-8")

    # Fleet defaults them to default_enabled: true
    fleet_cfg = {
        "mcp_servers": {
            "server1": {"command": "s1", "default_enabled": True},
            "server2": {"command": "s2", "default_enabled": True},
        }
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    # Rule E-1a: Op_L3=False, Mask=False -> False
    assert loaded["mcp_servers"]["server1"]["enabled"] is False
    # Rule E-1b: Op_L3=False, Mask=True -> False
    assert loaded["mcp_servers"]["server2"]["enabled"] is False


def test_rule_e2_operator_explicit_true_and_mask_override(tmp_path, monkeypatch):
    """Rule E-2a / E-2b: Operator explicit true wins unless binary is missing on node."""
    home = tmp_path / ".hermes"
    fleet_state = home / "state" / "fleet"
    fleet_state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Mask disables tool_missing
    (fleet_state / "mcp_availability.json").write_text(
        json.dumps({"disabled_servers": ["tool_missing"]}),
        encoding="utf-8",
    )

    user_cfg = {
        "mcp_servers": {
            "tool_available": {"enabled": True},
            "tool_missing": {"enabled": True},
        }
    }
    (home / "config.yaml").write_text(yaml.safe_dump(user_cfg), encoding="utf-8")

    fleet_cfg = {
        "mcp_servers": {
            "tool_available": {"command": "c1", "default_enabled": False},
            "tool_missing": {"command": "c2", "default_enabled": False},
        }
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    # Rule E-2a: Op_L3=True, Mask=False -> True (wins over fleet default_enabled: False)
    assert loaded["mcp_servers"]["tool_available"]["enabled"] is True
    # Rule E-2b: Op_L3=True, Mask=True -> False (missing binary overrides operator true)
    assert loaded["mcp_servers"]["tool_missing"]["enabled"] is False


def test_rule_e3_and_e4_operator_unset(tmp_path, monkeypatch):
    """Rules E-3, E-4a, E-4b: Operator unset respects fleet default unless binary is missing."""
    home = tmp_path / ".hermes"
    fleet_state = home / "state" / "fleet"
    fleet_state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Mask disables masked_srv1 and masked_srv2
    (fleet_state / "mcp_availability.json").write_text(
        json.dumps({"disabled_servers": ["masked_srv1", "masked_srv2"]}),
        encoding="utf-8",
    )

    # Operator expresses NO preference
    (home / "config.yaml").write_text("{}", encoding="utf-8")

    fleet_cfg = {
        "mcp_servers": {
            "unmasked_on": {"command": "c1", "default_enabled": True},
            "unmasked_off": {"command": "c2", "default_enabled": False},
            "masked_srv1": {"command": "c3", "default_enabled": True},
            "masked_srv2": {"command": "c4", "default_enabled": False},
        }
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    # Rule E-3: Op_L3=None, Mask=False -> F_def
    assert loaded["mcp_servers"]["unmasked_on"]["enabled"] is True
    assert loaded["mcp_servers"]["unmasked_off"]["enabled"] is False
    # Rule E-4a: Op_L3=None, Mask=True, F_def=True -> False
    assert loaded["mcp_servers"]["masked_srv1"]["enabled"] is False
    # Rule E-4b: Op_L3=None, Mask=True, F_def=False -> False
    assert loaded["mcp_servers"]["masked_srv2"]["enabled"] is False


def test_rule_e5_same_server_conflict_resolution(tmp_path, monkeypatch):
    """Rule E-5: Same-server conflict: mcp_overrides.<id>.enabled dominates mcp_servers.<id>.enabled."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    user_cfg = {
        "mcp_servers": {
            "conflict_server": {"enabled": True},
        },
        "mcp_overrides": {
            "conflict_server": {"enabled": False},
        }
    }
    (home / "config.yaml").write_text(yaml.safe_dump(user_cfg), encoding="utf-8")

    fleet_cfg = {
        "mcp_servers": {
            "conflict_server": {"command": "c1", "default_enabled": True},
        }
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    # mcp_overrides (False) wins over mcp_servers (True)
    assert loaded["mcp_servers"]["conflict_server"]["enabled"] is False


def test_object_schema_mask_normalization_a01(tmp_path, monkeypatch):
    """Critical A-01: Documented object-form disabled_servers entries must not crash with TypeError."""
    home = tmp_path / ".hermes"
    fleet_state = home / "state" / "fleet"
    fleet_state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Mask with mixed string and object forms
    (fleet_state / "mcp_availability.json").write_text(
        json.dumps({
            "disabled_servers": [
                {"server_id": "object_server", "reason": "missing binary", "effect": "enabled=false"},
                "string_server",
            ]
        }),
        encoding="utf-8",
    )

    fleet_cfg = {
        "mcp_servers": {
            "object_server": {"command": "o1", "default_enabled": True},
            "string_server": {"command": "s1", "default_enabled": True},
            "unaffected": {"command": "u1", "default_enabled": True},
        }
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    assert loaded["mcp_servers"]["object_server"]["enabled"] is False
    assert loaded["mcp_servers"]["string_server"]["enabled"] is False
    assert loaded["mcp_servers"]["unaffected"]["enabled"] is True


def test_cache_signature_invalidation_on_sealed_and_base_mask_a02(tmp_path, monkeypatch):
    """Critical A-02: Cache signature must include current/SEALED and current/mcp_availability.json."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    current_dir = home / "releases" / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = home / "config.yaml"
    cfg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    _, sig1 = _load_config_cache_sig(cfg_path)

    # Change current/SEALED
    (current_dir / "SEALED").write_text(json.dumps({"release_sha": "sha_1"}), encoding="utf-8")
    _, sig2 = _load_config_cache_sig(cfg_path)
    assert sig1 != sig2, "Cache signature must change when current/SEALED is modified"

    # Change current/mcp_availability.json
    (current_dir / "mcp_availability.json").write_text(json.dumps({"disabled_servers": ["t1"]}), encoding="utf-8")
    _, sig3 = _load_config_cache_sig(cfg_path)
    assert sig2 != sig3, "Cache signature must change when current/mcp_availability.json is modified"


def test_corrupt_active_mask_fallback_to_base_mask(tmp_path, monkeypatch):
    """Warning A-05: Corrupt JSON in active mask must gracefully fallback to base mask without crashing."""
    home = tmp_path / ".hermes"
    fleet_state = home / "state" / "fleet"
    fleet_state.mkdir(parents=True, exist_ok=True)
    current_dir = home / "releases" / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Base mask defines base_disabled
    (current_dir / "mcp_availability.json").write_text(
        json.dumps({"disabled_servers": ["base_disabled"]}),
        encoding="utf-8",
    )

    # Active mask has broken JSON
    (fleet_state / "mcp_availability.json").write_text("{broken json...", encoding="utf-8")

    fleet_cfg = {
        "mcp_servers": {
            "base_disabled": {"command": "c1", "default_enabled": True},
            "unaffected": {"command": "c2", "default_enabled": True},
        }
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    loaded = load_config()
    assert loaded["mcp_servers"]["base_disabled"]["enabled"] is False
    assert loaded["mcp_servers"]["unaffected"]["enabled"] is True


def test_config_yaml_comments_and_bytes_preserved_across_load(tmp_path, monkeypatch):
    """Warning A-05: Loading fleet overlay must never open or mutate config.yaml bytes or comments."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    raw_user_yaml = (
        "# Operator Configuration with Critical Comments\n"
        "# Do NOT delete or reorder these keys\n"
        "model:\n"
        "  default: cb-operator-model # Custom operator model\n"
        "\n"
        "# Security credentials section\n"
        "secrets:\n"
        "  vaultwarden:\n"
        "    collection: my-secret-col\n"
    )
    user_file = home / "config.yaml"
    user_file.write_text(raw_user_yaml, encoding="utf-8")

    fleet_cfg = {
        "terminal": {"timeout": 600}
    }
    (home / "config.fleet.yaml").write_text(yaml.safe_dump(fleet_cfg), encoding="utf-8")

    # Load configuration
    loaded = load_config()
    assert loaded["model"]["default"] == "cb-operator-model"
    assert loaded["terminal"]["timeout"] == 600

    # Assert config.yaml bytes and comments remain 100% byte-identical
    after_bytes = user_file.read_text(encoding="utf-8")
    assert after_bytes == raw_user_yaml, "config.yaml was mutated or comments were lost!"
