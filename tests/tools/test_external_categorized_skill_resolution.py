"""Regression tests for external categorized skill resolution and governance parity (C1.1 / C1.2)."""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import hermes_yaml as yaml
import pytest

from agent import skill_utils as su
from hermes_cli import write_approval_commands as wac
from tools import skill_manager_tool as smt
from tools import write_approval as wa


def _write_skill(root: Path, rel_path: str, *, description: str = "Demo categorized skill", extra: str = "") -> Path:
    skill_dir = root / Path(rel_path)
    skill_dir.mkdir(parents=True, exist_ok=True)
    name = skill_dir.name
    content = textwrap.dedent(
        f"""\
        ---
        name: {name}
        description: {description}
        ---
        # {name}

        ## When to Use
        Use when testing {rel_path}.
        {extra}
        """
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


@pytest.fixture()
def skill_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    local_skills = home / "skills"
    create_dir = tmp_path / "create-skills"
    ext1 = tmp_path / "ext-one"
    ext2 = tmp_path / "ext-two"
    for d in (local_skills, create_dir, ext1, ext2):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("tools.skills_tool.SKILLS_DIR", local_skills)
    cfg = {
        "skills": {
            "create_dir": str(create_dir),
            "external_dirs": [str(ext1), str(ext2)],
        }
    }
    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    su._external_dirs_cache_clear()
    yield {
        "tmp": tmp_path,
        "home": home,
        "local": local_skills,
        "create": create_dir,
        "ext1": ext1,
        "ext2": ext2,
    }
    su._external_dirs_cache_clear()


def test_find_skill_resolves_external_categorized_skill_and_normalizes_backslash(skill_env):
    expected_cr = _write_skill(skill_env["ext1"], "engineering/code-review")
    expected_up = _write_skill(skill_env["ext2"], "devops/hermes-upgrade-verification")

    found_cr = smt._find_skill("engineering/code-review")
    assert found_cr is not None
    assert Path(found_cr["path"]).resolve() == expected_cr.resolve()

    found_win = smt._find_skill(r"devops\hermes-upgrade-verification")
    assert found_win is not None
    assert Path(found_win["path"]).resolve() == expected_up.resolve()


def test_find_skill_rejects_path_traversal_and_absolute_escape(skill_env):
    outside = _write_skill(skill_env["tmp"] / "outside", "secret-skill")
    _write_skill(skill_env["ext1"], "engineering/code-review")

    assert smt._find_skill("../outside/secret-skill") is None
    assert smt._find_skill("engineering/../../outside/secret-skill") is None
    assert smt._find_skill(r"engineering\..\..\outside\secret-skill") is None
    assert smt._find_skill(str(outside)) is None
    assert wac._find_canonical_skill_matches("../outside/secret-skill") == []
    assert wac._find_canonical_skill_matches("engineering/../../outside/secret-skill") == []


def test_find_skill_tier_precedence_and_same_tier_divergent_collision_refusal(skill_env, caplog):
    ext1_dir = _write_skill(skill_env["ext1"], "engineering/code-review", extra="Variant A")
    ext2_dir = _write_skill(skill_env["ext2"], "engineering/code-review", extra="Variant B")

    # Same-tier divergent collision in external_dirs refuses to guess in _find_skill
    with caplog.at_level(logging.WARNING, logger="tools.skill_manager_tool"):
        assert smt._find_skill("engineering/code-review") is None
    assert any("engineering/code-review" in r.getMessage() for r in caplog.records)

    # Governance resolver surfaces BOTH candidates so preflight marks BLOCKED_RESOLVER
    matches = wac._find_canonical_skill_matches("engineering/code-review")
    assert {p.resolve() for p in matches} == {ext1_dir.resolve(), ext2_dir.resolve()}

    # Higher-precedence local copy wins cleanly over external collision
    local_dir = _write_skill(skill_env["local"], "engineering/code-review", extra="Local authoritative")
    resolved = smt._find_skill("engineering/code-review")
    assert resolved is not None
    assert Path(resolved["path"]).resolve() == local_dir.resolve()
    assert wac._find_canonical_skill_matches("engineering/code-review") == [local_dir.resolve()]


def test_preflight_and_layer0_resolve_external_categorized_skill_and_reject_missing(skill_env, monkeypatch):
    ext_skill = _write_skill(skill_env["ext1"], "engineering/code-review", extra="Original line")

    # Mock Layer 0 evaluator to pass deterministically without external gate script dependency
    monkeypatch.setattr(
        wac,
        "_run_layer0_eval",
        lambda skill_dir, timeout_s=5.0: {
            "skill_name": "code-review",
            "skill_dir": str(skill_dir),
            "tier": 1,
            "passed": True,
            "effective_desc_length": 30,
            "max_desc_budget": 60,
            "front_loaded_trigger_ok": True,
            "linter_errors": (),
            "guard_verdict": "safe",
            "guard_findings": (),
            "tier1_static_findings": (),
            "shadow_status": "active",
            "nerve_admission_recall": 1.0,
            "provider_calls": 0,
        },
    )

    rec = wa.stage_write(
        wa.SKILLS,
        {
            "action": "patch",
            "name": "engineering/code-review",
            "old_string": "Original line",
            "new_string": "Updated line",
        },
        summary="patch external categorized skill",
        origin="background_review",
    )

    preflight = wac.preflight_pending_skill_review()
    item = preflight["records"][0]
    assert item["pending_id"] == rec["id"]
    assert item["effective_status"] != "BLOCKED_RESOLVER"
    assert item["layer0_passed"] is True
    assert Path(item["canonical_target"]).resolve() == ext_skill.resolve()

    # Non-existent categorized target returns [] without matching same-basename skill elsewhere
    _write_skill(skill_env["ext1"], "other/missing-skill")
    assert wac._find_canonical_skill_matches("nonexistent/missing-skill") == []
