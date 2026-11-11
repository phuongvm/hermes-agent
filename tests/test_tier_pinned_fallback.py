"""Slice 4 / Module D4 — TierPinnedFallbackPolicy (Hermes client side).

Red-loop contract (design.md §5.4):
  * ``enforce_tier1_response_guard`` raises ``TierFloorViolationError`` when a specialist profile's response
    carries an ``x-model-id`` outside ``TIER_1_ALLOWLIST`` (or none at all when the header is required);
  * the retry schedule after a violation / 503 is ``(15, 30, 60, 120)`` then ``WAIT(120s)`` — never a cheap-tier
    downgrade;
  * ``verify_9router_combo_readback`` checks the 5 specialist combos and writes
    ``9router_combo_readback_receipt.json``; with ``NINEROUTER_READBACK_FIXTURE_PATH`` it is fully offline;
  * ``audit_specialist_cheap_model`` proves ``cheap_model: cb-always-on`` is enabled for the default profile only.
All fixtures live in ``tmp_path``; zero network, zero LLM tokens.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import tier_floor_policy as tfp

SPECIALISTS = ("cb-agent-coder", "cb-agent-reviewer", "cb-agent-leader", "cb-agent-designer", "cb-agent-qa")


# ---------------------------------------------------------------------------- allowlist / guard
def test_allowlist_names_specialists_and_excludes_tier3():
    assert set(SPECIALISTS) <= set(tfp.TIER_1_ALLOWLIST)
    for bad in ("nemotron-free", "crouton-alpha", "cb-always-on", "nvidia/nemotron-3-ultra:free", "cb-nous-free"):
        assert not tfp.is_tier1_model_id(bad), bad
    for good in ("cb-gemini-argon", "cb-claude-opus", "cc/claude-opus-4-1", "ag/gemini-3-pro-preview"):
        assert tfp.is_tier1_model_id(good), good


@pytest.mark.parametrize("bad", ["nemotron-free", "crouton-alpha", "cb-always-on"])
def test_guard_raises_on_tier3_model_id(bad):
    with pytest.raises(tfp.TierFloorViolationError) as ei:
        tfp.enforce_tier1_response_guard({"x-model-id": bad}, profile_model="cb-agent-coder")
    assert ei.value.model_id == bad
    assert ei.value.profile_model == "cb-agent-coder"
    assert "TIER_1" in str(ei.value)


def test_guard_passes_tier1_and_is_noop_for_non_specialist():
    assert tfp.enforce_tier1_response_guard({"X-Model-Id": "cb-claude-opus"}, profile_model="cb-agent-reviewer") == "cb-claude-opus"
    # default profile (cb-always-on) is not tier pinned -> never raises
    assert tfp.enforce_tier1_response_guard({"x-model-id": "nemotron-free"}, profile_model="cb-always-on") is None
    # missing provenance on a specialist: rejected fail-closed
    with pytest.raises(tfp.TierFloorViolationError) as ei:
        tfp.enforce_tier1_response_guard({}, profile_model="cb-agent-qa")
    assert "missing provenance" in str(ei.value)
    with pytest.raises(tfp.TierFloorViolationError):
        tfp.enforce_tier1_response_guard({}, profile_model="cb-agent-qa", require_header=True)


def test_guard_rejects_response_model_body_even_with_empty_headers():
    from types import SimpleNamespace
    with pytest.raises(tfp.TierFloorViolationError) as ei:
        tfp.enforce_tier1_response_guard(
            SimpleNamespace(headers={}, model="nemotron-free"),
            profile_model="cb-agent-coder",
        )
    assert ei.value.model_id == "nemotron-free"
    assert ei.value.profile_model == "cb-agent-coder"


def test_guard_accepts_header_objects_and_response_like():
    class H:  # httpx-style headers (case-insensitive .get)
        def get(self, k, d=None):
            return {"x-model-id": "cb-gemini-argon"}.get(k.lower(), d)

    class R:
        headers = H()

    assert tfp.enforce_tier1_response_guard(R(), profile_model="cb-agent-leader") == "cb-gemini-argon"


# ---------------------------------------------------------------------------- backoff schedule
def test_backoff_schedule_is_15_30_60_120_then_wait_120():
    assert tfp.TIER1_BACKOFF_SCHEDULE_S == (15, 30, 60, 120)
    assert tfp.TIER1_WAIT_S == 120
    assert [tfp.tier1_backoff_for_attempt(i) for i in range(7)] == [15, 30, 60, 120, 120, 120, 120]
    # a Retry-After hint never shortens below the schedule step and never exceeds the WAIT ceiling
    assert tfp.tier1_backoff_for_attempt(0, retry_after_s=3) == 15
    assert tfp.tier1_backoff_for_attempt(0, retry_after_s=90) == 90
    assert tfp.tier1_backoff_for_attempt(1, retry_after_s=600) == 120


def test_is_tier_pinned_profile_model():
    for m in SPECIALISTS:
        assert tfp.is_tier_pinned_model(m)
    assert not tfp.is_tier_pinned_model("cb-always-on")
    assert not tfp.is_tier_pinned_model(None)


# ---------------------------------------------------------------------------- 9router readback
def _fixture(tmp_path: Path, combos):
    p = tmp_path / "combos_fixture.json"
    p.write_text(json.dumps(combos), encoding="utf-8")
    return p


def test_readback_passes_with_offline_fixture_and_writes_receipt(tmp_path, monkeypatch):
    combos = [{"id": f"id-{n}", "name": n, "models": ["cb-gemini-argon", "cb-claude-opus"],
               "fallback_tier_floor": "TIER_1_FRONTIER_REASONING"} for n in SPECIALISTS]
    combos.append({"id": "id-x", "name": "cb-always-on", "models": ["nemotron-free"], "fallback_tier_floor": None})
    monkeypatch.setenv("NINEROUTER_READBACK_FIXTURE_PATH", str(_fixture(tmp_path, combos)))
    receipt_path = tmp_path / "9router_combo_readback_receipt.json"
    receipt = tfp.verify_9router_combo_readback(base_url="http://127.0.0.1:9", receipt_path=receipt_path)
    assert receipt["passed"] is True
    assert receipt["source"] == "fixture"
    assert receipt["mode"] == "offline_fixture"
    assert sorted(receipt["verified"]) == sorted(SPECIALISTS)
    assert receipt["combos_verified"] == 5
    assert "per_combo_digests" in receipt
    assert receipt["violations"] == []
    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert on_disk["passed"] is True and on_disk["combo_count"] == 5
    assert on_disk["combos_verified"] == 5
    assert on_disk["mode"] == "offline_fixture"
    assert "per_combo_digests" in on_disk
    assert "fixture_sha256" in on_disk and len(on_disk["fixture_sha256"]) == 64


def test_combo_violations_rejects_empty_models():
    combos = [{"name": n, "fallback_tier_floor": tfp.TIER_1_FRONTIER_REASONING, "models": []} for n in tfp.SPECIALIST_COMBO_IDS]
    violations = tfp._combo_violations(combos)
    assert len(violations) == len(tfp.SPECIALIST_COMBO_IDS)
    assert all(v["reason_code"] == "EMPTY_MODELS" for v in violations)


def test_readback_rejects_missing_floor_or_tier3_member(tmp_path, monkeypatch):
    combos = [{"id": f"id-{n}", "name": n, "models": ["cb-gemini-argon"], "fallback_tier_floor": "TIER_1_FRONTIER_REASONING"}
              for n in SPECIALISTS]
    combos[0]["fallback_tier_floor"] = None                      # coder: floor missing
    combos[1]["models"] = ["cb-gemini-argon", "nemotron-free"]    # reviewer: TIER_3 member
    del combos[4]                                                 # qa: combo absent
    monkeypatch.setenv("NINEROUTER_READBACK_FIXTURE_PATH", str(_fixture(tmp_path, combos)))
    with pytest.raises(tfp.TierFloorReadbackError) as exc_info:
        tfp.verify_9router_combo_readback(base_url="http://127.0.0.1:9", receipt_path=tmp_path / "r.json")
    receipt = exc_info.value.receipt
    assert receipt is not None
    assert receipt["passed"] is False
    codes = {v["combo"]: v["reason_code"] for v in exc_info.value.violations}
    assert codes == {"cb-agent-coder": "MISSING_TIER_FLOOR", "cb-agent-reviewer": "TIER_3_MEMBER", "cb-agent-qa": "COMBO_ABSENT"}


def test_readback_without_fixture_and_unreachable_router_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("NINEROUTER_READBACK_FIXTURE_PATH", raising=False)
    with pytest.raises(tfp.TierFloorReadbackError) as exc_info:
        tfp.verify_9router_combo_readback(base_url="http://127.0.0.1:9", receipt_path=tmp_path / "r.json",
                                          fetch=lambda url, headers: (_ for _ in ()).throw(OSError("refused")))
    receipt = exc_info.value.receipt
    assert receipt is not None
    assert receipt["passed"] is False
    assert receipt["source"] == "live"
    assert exc_info.value.violations[0]["reason_code"] == "ROUTER_UNREACHABLE"
    assert (tmp_path / "r.json").exists()


# ---------------------------------------------------------------------------- config audit
def _write_cfg(path: Path, *, model: str, enabled: bool, cheap: str = "cb-always-on"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"model:\n  default: {model}\n  provider: ai-router\n"
        f"smart_model_routing:\n  enabled: {'true' if enabled else 'false'}\n"
        f"  cheap_model:\n    model: {cheap}\n    provider: ai-router\n"
        "auxiliary:\n  compression:\n    provider: auto\n    model: ''\n",
        encoding="utf-8")


def test_config_audit_requires_cheap_model_disabled_on_specialists(tmp_path):
    root = tmp_path / "hermes"
    _write_cfg(root / "config.yaml", model="cb-always-on", enabled=True)
    for p in ("coder", "reviewer", "leader", "designer", "qa"):
        _write_cfg(root / "profiles" / p / "config.yaml", model=f"cb-agent-{p}", enabled=False)
    report = tfp.audit_specialist_cheap_model(root)
    assert report["passed"] is True and report["violations"] == []
    assert report["default_profile"]["cheap_model_enabled"] is True

    _write_cfg(root / "profiles" / "coder" / "config.yaml", model="cb-agent-coder", enabled=True)
    report = tfp.audit_specialist_cheap_model(root)
    assert report["passed"] is False
    assert report["violations"] == [{"profile": "coder", "reason_code": "CHEAP_MODEL_ENABLED_ON_SPECIALIST",
                                     "cheap_model": "cb-always-on"}]


def test_module_cli_audit_runs_against_tmp_root(tmp_path):
    """The real fleet config is audited via the CLI (`python -m agent.tier_floor_policy --audit-config <root>`);
    tests must not touch the live HERMES_HOME (home_io_guard), so the CLI is exercised on a tmp root here."""
    root = tmp_path / "hermes"
    _write_cfg(root / "config.yaml", model="cb-always-on", enabled=True)
    _write_cfg(root / "profiles" / "qa" / "config.yaml", model="cb-agent-qa", enabled=True)
    rc = tfp.main(["--audit-config", str(root), "--json"])
    assert rc == 2
    _write_cfg(root / "profiles" / "qa" / "config.yaml", model="cb-agent-qa", enabled=False)
    assert tfp.main(["--audit-config", str(root)]) == 0


# ---------------------------------------------------------------------------- wiring into the three seams
class _Agent:
    def __init__(self, model, chain):
        self.model = model
        self.provider = "ai-router"
        self.base_url = "http://router.local/v1"
        self.quiet_mode = True
        self._fallback_chain = list(chain)
        self._fallback_index = 0
        self._tier1_violation_attempts = 0
        self.goal_manager: Any = None


def test_init_fallback_chain_drops_tier3_entries_for_specialist_profile():
    from agent.agent_init_fallback import _fallback_entries, filter_fallback_entries_for_tier_floor
    chain = _fallback_entries([{"provider": "ai-router", "model": "cb-claude-opus"},
                               {"provider": "ai-router", "model": "cb-always-on"},
                               {"provider": "ai-router", "model": "nemotron-free"}])
    kept = filter_fallback_entries_for_tier_floor(chain, primary_model="cb-agent-coder")
    assert [e["model"] for e in kept] == ["cb-claude-opus"]
    # default profile keeps the legacy chain untouched
    assert filter_fallback_entries_for_tier_floor(chain, primary_model="cb-always-on") == chain


def test_should_skip_fallback_candidate_refuses_tier3_on_specialist():
    from agent.tier_floor_policy import tier_floor_blocks_fallback
    agent = _Agent("cb-agent-reviewer", [])
    assert tier_floor_blocks_fallback(agent, "ai-router", "cb-always-on") is True
    assert tier_floor_blocks_fallback(agent, "ai-router", "cb-claude-opus") is False
    assert tier_floor_blocks_fallback(_Agent("cb-always-on", []), "ai-router", "nemotron-free") is False


def test_turn_api_error_tier_floor_backoff_overrides_generic_wait():
    from agent.turn_api_error import tier_floor_wait_override
    agent = _Agent("cb-agent-qa", [])
    err = tfp.TierFloorViolationError(model_id="nemotron-free", profile_model="cb-agent-qa")
    waits = [tier_floor_wait_override(agent, err, status_code=None) for _ in range(5)]
    assert waits == [15, 30, 60, 120, 120]
    # 503 from a pinned combo follows the same schedule (attempt counter continues)
    class E(Exception):
        status_code = 503
    assert tier_floor_wait_override(agent, E(), status_code=503) == 120
    # 429 also triggers the tier floor backoff
    class E429(Exception):
        status_code = 429
    agent429 = _Agent("cb-agent-coder", [])
    assert tier_floor_wait_override(agent429, E429(), status_code=429) == 15
    # unrelated errors / non-pinned profiles: no override
    assert tier_floor_wait_override(agent, ValueError("x"), status_code=400) is None
    assert tier_floor_wait_override(_Agent("cb-always-on", []), err, status_code=503) is None
    assert tier_floor_wait_override(_Agent("cb-always-on", []), E429(), status_code=429) is None


def test_tier_floor_wait_override_transitions_goal_to_wait_120():
    from agent.turn_api_error import tier_floor_wait_override
    from unittest.mock import MagicMock
    agent = _Agent("cb-agent-coder", [])
    gm = MagicMock()
    gm.is_active.return_value = True
    agent.goal_manager = gm
    class E429(Exception):
        status_code = 429
        response = MagicMock(headers={"retry-after": "120"})
    wait = tier_floor_wait_override(agent, E429(), status_code=429)
    assert wait == 120
    gm.wait_for_seconds.assert_called_once()
    assert gm.wait_for_seconds.call_args[0][0] == 120


# ---------------------------------------------------------------------------- tasks.md 4.3 proof names
# The OpenSpec proof command selects these exact test ids with ``-k``; each delegates to the contract above so
# the proof string and the fine-grained tests can never drift apart.
def test_9router_combo_readback_all_five_specialists_and_offline_fixture_contract(tmp_path, monkeypatch):
    test_readback_passes_with_offline_fixture_and_writes_receipt(tmp_path, monkeypatch)


def test_9router_combo_readback_fails_closed_on_missing_floor_or_non_tier1_model(tmp_path, monkeypatch):
    test_readback_rejects_missing_floor_or_tier3_member(tmp_path, monkeypatch)
    test_readback_without_fixture_and_unreachable_router_fails_closed(tmp_path, monkeypatch)


def test_9router_combo_readback_and_tier1_backoff_on_503(tmp_path, monkeypatch):
    """A 503 from a tier-pinned combo parks the agent on (15, 30, 60, 120) then WAIT(120s); the readback
    receipt still records the floor contract — the agent never swaps in a TIER_3 model."""
    from agent.turn_api_error import tier_floor_wait_override

    class Resp:
        headers = {"retry-after": "45"}

    class E(Exception):
        status_code = 503
        response = Resp()

    agent = _Agent("cb-agent-coder", [])
    assert [tier_floor_wait_override(agent, E(), status_code=503) for _ in range(5)] == [45, 45, 60, 120, 120]
    test_readback_passes_with_offline_fixture_and_writes_receipt(tmp_path, monkeypatch)


def test_response_model_tier_guard_rejects_downgraded_x_model_id():
    for bad in ("nemotron-free", "crouton-alpha", "cb-always-on"):
        test_guard_raises_on_tier3_model_id(bad)
    test_guard_passes_tier1_and_is_noop_for_non_specialist()
