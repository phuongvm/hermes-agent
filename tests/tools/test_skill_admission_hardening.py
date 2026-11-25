"""TDD admission & staging hardening tests for autonomous background_review skill proposals."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _skill_md(name: str, body: str = "Initial rule.") -> str:
    return (
        f"---\nname: {name}\n"
        f"description: Use when testing {name}. Deterministic rule.\n"
        f"---\n\n# {name}\n\n{body}\n"
    )


def _make_skill(root: Path, name: str, body: str = "Initial rule.") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_skill_md(name, body), encoding="utf-8")
    return skill_dir


def _link_dir_or_junction(link_path: Path, target_dir: Path) -> None:
    """Create a directory symlink, falling back to an NTFS junction on unprivileged Windows."""
    try:
        link_path.symlink_to(target_dir, target_is_directory=True)
    except OSError:
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(target_dir.resolve()), str(link_path))
        else:
            raise


@contextmanager
def _as_background_review():
    from tools.skill_manager_guards import _reset_background_review_read_marks
    from tools.skill_provenance import reset_current_write_origin, set_current_write_origin

    token = set_current_write_origin("background_review")
    _reset_background_review_read_marks()
    try:
        yield
    finally:
        reset_current_write_origin(token)


@pytest.fixture
def admission_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / ".hermes"
    local_dir = home / "skills"
    local_dir.mkdir(parents=True)
    create_dir = tmp_path / "workspace-skills"
    create_dir.mkdir(parents=True)
    ro_dir = tmp_path / "third-party-skills"
    ro_dir.mkdir(parents=True)
    outside_cwd = tmp_path / "outside-cwd"
    outside_cwd.mkdir(parents=True)

    monkeypatch.setenv("HERMES_HOME", str(home))
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "_hermes_home_cache", None, raising=False)
    (home / "config.yaml").write_text(
        "skills:\n"
        "  write_approval: true\n"
        f"  create_dir: {create_dir.as_posix()}\n"
        "  external_dirs:\n"
        f"    - {create_dir.as_posix()}\n"
        f"    - {ro_dir.as_posix()}\n"
        "  read_only_dirs:\n"
        f"    - {ro_dir.as_posix()}\n"
        f"    - {(tmp_path / 'missing-ro-dir').as_posix()}\n",
        encoding="utf-8",
    )

    from agent import skill_utils as su
    from tools import skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", local_dir)
    monkeypatch.chdir(outside_cwd)
    su._raw_config_cache_clear()
    su._external_dirs_cache_clear()
    yield {
        "home": home,
        "local_dir": local_dir,
        "create_dir": create_dir,
        "ro_dir": ro_dir,
    }
    su._raw_config_cache_clear()
    su._external_dirs_cache_clear()


def test_import_origin_resolves_inside_this_checkout() -> None:
    import agent.background_review as br
    import agent.skill_utils as su
    import tools.skill_manager_batch as smb
    import tools.skill_manager_guards as smg
    import tools.skill_manager_tool as smt
    import tools.write_approval as wa

    for mod in (su, br, smg, smb, smt, wa):
        mod_path = Path(mod.__file__).resolve()
        assert mod_path.is_relative_to(REPO_ROOT), (
            f"{mod.__name__} imported from {mod_path}, expected under {REPO_ROOT}"
        )


def test_read_only_dirs_preserve_create_dir_writability_and_external_ownership(
    admission_env: dict[str, Path],
) -> None:
    from agent import skill_utils as su
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    ws_skill = _make_skill(admission_env["create_dir"], "workspace-owned")
    ro_skill = _make_skill(admission_env["ro_dir"], "mattpocock-tdd")

    assert su.get_read_only_skills_dirs() == [admission_env["ro_dir"].resolve()]
    # Both live in external/create roots so is_external_skill_path stays True for both:
    assert su.is_external_skill_path(ws_skill / "SKILL.md") is True
    assert su.is_external_skill_path(ro_skill / "SKILL.md") is True
    # Only skills.read_only_dirs resolves True under is_read_only_skill_path:
    assert su.is_read_only_skill_path(ws_skill / "SKILL.md") is False
    assert su.is_read_only_skill_path(ro_skill / "SKILL.md") is True

    with _as_background_review():
        # Workspace create_dir remains writable for autonomous patches, while delete stays blocked:
        patched = json.loads(
            smt.skill_manage(
                action="",
                name="",
                operations=[
                    {
                        "action": "patch",
                        "name": "workspace-owned",
                        "old_string": "Initial rule.",
                        "new_string": "Updated rule.",
                    }
                ],
            )
        )
        assert patched["success"] is True and patched["staged"] is True
        assert wa.pending_count(wa.SKILLS) == 1

        del_res = json.loads(
            smt.skill_manage(
                action="delete",
                name="workspace-owned",
                absorbed_into="workspace-owned",
            )
        )
        assert del_res["success"] is False
        assert wa.pending_count(wa.SKILLS) == 1


def test_read_only_external_blocks_all_background_review_mutations_including_links(
    admission_env: dict[str, Path],
) -> None:
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    ro_skill = _make_skill(admission_env["ro_dir"], "code-review")
    (ro_skill / "references").mkdir(exist_ok=True)
    (ro_skill / "references" / "notes.md").write_text("ref\n", encoding="utf-8")

    # Mount a junction/symlink inside local skills pointing into read_only_dirs
    linked_skill = admission_env["local_dir"] / "linked-ro-skill"
    _link_dir_or_junction(linked_skill, ro_skill)

    with _as_background_review():
        for target_name in ("code-review", "linked-ro-skill"):
            for kwargs in (
                {
                    "action": "patch",
                    "name": target_name,
                    "old_string": "Initial rule.",
                    "new_string": "Mutated.",
                },
                {
                    "action": "edit",
                    "name": target_name,
                    "content": _skill_md(target_name, "Rewritten."),
                },
                {
                    "action": "write_file",
                    "name": target_name,
                    "file_path": "references/new.md",
                    "file_content": "doc\n",
                },
                {
                    "action": "remove_file",
                    "name": target_name,
                    "file_path": "references/notes.md",
                },
                {
                    "action": "delete",
                    "name": target_name,
                    "absorbed_into": "some-umbrella",
                },
                {
                    "action": "",
                    "name": "",
                    "operations": [
                        {
                            "action": "patch",
                            "name": target_name,
                            "old_string": "Initial rule.",
                            "new_string": "Batch mutated.",
                        }
                    ],
                },
            ):
                res = json.loads(smt.skill_manage(**kwargs))
                assert res["success"] is False, (target_name, kwargs, res)
                assert res.get("_read_only_external") is True, (target_name, kwargs, res)

    assert wa.pending_count(wa.SKILLS) == 0


def test_read_only_external_allows_foreground_edits(
    admission_env: dict[str, Path],
) -> None:
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    _make_skill(admission_env["ro_dir"], "code-review")

    res = json.loads(
        smt.skill_manage(
            action="patch",
            name="code-review",
            old_string="Initial rule.",
            new_string="User-directed foreground edit.",
        )
    )
    assert res["success"] is True
    assert res["staged"] is True
    assert wa.pending_count(wa.SKILLS) == 1


# ---------------------------------------------------------------------------
# Task 1.2: Preflight Consolidation Delete Verification Before Staging
# ---------------------------------------------------------------------------


def _make_curator_managed_local_skill(local_dir: Path, name: str) -> Path:
    from tools import skill_usage

    skill_dir = _make_skill(local_dir, name)
    skill_usage.record_created(name, agent_created=True)
    return skill_dir


@pytest.mark.parametrize("bad_umbrella", [None, "", "   "])
@pytest.mark.parametrize("use_batch_shape", [False, True])
def test_delete_preflight_refuses_bare_autonomous_delete_before_staging(
    admission_env: dict[str, Path],
    bad_umbrella: str | None,
    use_batch_shape: bool,
) -> None:
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    _make_curator_managed_local_skill(admission_env["local_dir"], "curator-alpha")

    with _as_background_review():
        if use_batch_shape:
            op: dict[str, str | None] = {"action": "delete", "name": "curator-alpha"}
            if bad_umbrella is not None:
                op["absorbed_into"] = bad_umbrella
            res = json.loads(smt.skill_manage(action="", name="", operations=[op]))
        else:
            res = json.loads(
                smt.skill_manage(
                    action="delete",
                    name="curator-alpha",
                    absorbed_into=bad_umbrella,
                )
            )

    assert res["success"] is False
    assert res.get("_fail_closed") is True
    assert wa.pending_count(wa.SKILLS) == 0


@pytest.mark.parametrize("bad_target", ["missing-umbrella", "curator-alpha"])
@pytest.mark.parametrize("use_batch_shape", [False, True])
def test_delete_preflight_refuses_missing_or_self_umbrella_before_staging(
    admission_env: dict[str, Path],
    bad_target: str,
    use_batch_shape: bool,
) -> None:
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    _make_curator_managed_local_skill(admission_env["local_dir"], "curator-alpha")

    with _as_background_review():
        if use_batch_shape:
            res = json.loads(
                smt.skill_manage(
                    action="",
                    name="",
                    operations=[
                        {
                            "action": "delete",
                            "name": "curator-alpha",
                            "absorbed_into": bad_target,
                        }
                    ],
                )
            )
        else:
            res = json.loads(
                smt.skill_manage(
                    action="delete",
                    name="curator-alpha",
                    absorbed_into=bad_target,
                )
            )

    assert res["success"] is False
    assert res.get("_fail_closed") is True
    assert bad_target in res["error"]
    assert wa.pending_count(wa.SKILLS) == 0


def test_delete_preflight_stages_valid_umbrella_consolidation_and_keeps_foreground_bare_delete(
    admission_env: dict[str, Path],
) -> None:
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    _make_curator_managed_local_skill(admission_env["local_dir"], "curator-alpha")
    _make_curator_managed_local_skill(admission_env["local_dir"], "curator-beta")
    _make_skill(admission_env["local_dir"], "umbrella-skill")

    with _as_background_review():
        staged = json.loads(
            smt.skill_manage(
                action="delete",
                name="curator-alpha",
                absorbed_into="umbrella-skill",
            )
        )
    assert staged["success"] is True
    assert staged["staged"] is True
    rec = wa.get_pending(wa.SKILLS, staged["pending_id"])
    assert rec is not None
    assert rec["payload"]["action"] == "delete"
    assert rec["payload"]["name"] == "curator-alpha"
    assert rec["payload"]["absorbed_into"] == "umbrella-skill"

    # Foreground bare delete without absorbed_into remains permitted to stage:
    fg_staged = json.loads(
        smt.skill_manage(action="delete", name="curator-beta")
    )
    assert fg_staged["success"] is True
    assert fg_staged["staged"] is True
    assert wa.pending_count(wa.SKILLS) == 2


# ---------------------------------------------------------------------------
# Task 1.3: Per-Target Batch Staging Split & Duplicate Pending Admission Fence
# ---------------------------------------------------------------------------


def test_pending_skill_targets_and_duplicate_lookup_cover_flat_and_batch_records(
    admission_env: dict[str, Path],
) -> None:
    from tools import write_approval as wa

    r1 = wa.stage_write(
        wa.SKILLS,
        {"action": "patch", "name": "alpha", "old_string": "a", "new_string": "b"},
        summary="patch alpha",
        origin="background_review",
    )
    r2 = wa.stage_write(
        wa.SKILLS,
        {
            "action": "batch",
            "operations": [
                {"action": "patch", "name": "beta", "old_string": "x", "new_string": "y"},
                {"action": "write_file", "name": "alpha", "file_path": "references/r.md", "file_content": "z"},
            ],
        },
        summary="batch beta+alpha",
        origin="background_review",
    )

    targets = wa.pending_skill_targets()
    assert targets == {
        "alpha": [r1["id"], r2["id"]],
        "beta": [r2["id"]],
    }
    assert wa.find_duplicate_pending_skills(["gamma", "alpha"]) == {
        "alpha": [r1["id"], r2["id"]]
    }


def test_cross_skill_background_review_batch_splits_into_per_target_records(
    admission_env: dict[str, Path],
) -> None:
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    _make_skill(admission_env["local_dir"], "alpha", "Alpha step 1.")
    _make_skill(admission_env["local_dir"], "beta", "Beta step 1.")

    with _as_background_review():
        res = json.loads(
            smt.skill_manage(
                action="",
                name="",
                operations=[
                    {
                        "action": "patch",
                        "name": "alpha",
                        "old_string": "Alpha step 1.",
                        "new_string": "Alpha step 2.",
                    },
                    {
                        "action": "patch",
                        "name": "beta",
                        "old_string": "Beta step 1.",
                        "new_string": "Beta step 2.",
                    },
                    {
                        "action": "write_file",
                        "name": "alpha",
                        "file_path": "references/guide.md",
                        "file_content": "# Alpha Guide\n",
                    },
                ],
            )
        )

    assert res["success"] is True
    assert res["staged"] is True
    assert res["split_targets"] == ["alpha", "beta"]
    assert res["skipped_duplicates"] == {}
    assert len(res["pending_ids"]) == 2
    assert res["pending_id"] == res["pending_ids"][0]
    assert wa.pending_count(wa.SKILLS) == 2

    rec_alpha = wa.get_pending(wa.SKILLS, res["pending_ids"][0])
    rec_beta = wa.get_pending(wa.SKILLS, res["pending_ids"][1])
    assert rec_alpha is not None and rec_beta is not None
    assert rec_alpha["payload"]["action"] == "batch"
    assert [op["action"] for op in rec_alpha["payload"]["operations"]] == ["patch", "write_file"]
    assert all(op["name"] == "alpha" for op in rec_alpha["payload"]["operations"])
    assert [op["action"] for op in rec_beta["payload"]["operations"]] == ["patch"]
    assert all(op["name"] == "beta" for op in rec_beta["payload"]["operations"])

    # Independent replay of alpha succeeds without touching beta:
    replay_alpha = json.loads(smt.apply_skill_pending(rec_alpha["payload"], origin="background_review"))
    assert replay_alpha["success"] is True
    assert "Alpha step 2." in (admission_env["local_dir"] / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "Beta step 1." in (admission_env["local_dir"] / "beta" / "SKILL.md").read_text(encoding="utf-8")


def test_single_skill_and_foreground_cross_skill_batches_stage_as_single_record(
    admission_env: dict[str, Path],
) -> None:
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    with _as_background_review():
        single = json.loads(
            smt.skill_manage(
                action="",
                name="",
                operations=[
                    {"action": "create", "name": "probe-one", "content": _skill_md("probe-one")},
                    {
                        "action": "write_file",
                        "name": "probe-one",
                        "file_path": "references/a.md",
                        "file_content": "note\n",
                    },
                ],
            )
        )
    assert single["success"] is True
    assert single["staged"] is True
    assert "split_targets" not in single
    assert wa.pending_count(wa.SKILLS) == 1

    # Foreground cross-skill batch stages as ONE atomic pending record:
    _make_skill(admission_env["local_dir"], "fg-alpha")
    _make_skill(admission_env["local_dir"], "fg-beta")
    fg = json.loads(
        smt.skill_manage(
            action="",
            name="",
            operations=[
                {"action": "patch", "name": "fg-alpha", "old_string": "Initial rule.", "new_string": "FG A."},
                {"action": "patch", "name": "fg-beta", "old_string": "Initial rule.", "new_string": "FG B."},
            ],
        )
    )
    assert fg["success"] is True
    assert fg["staged"] is True
    assert "split_targets" not in fg
    assert wa.pending_count(wa.SKILLS) == 2


def test_duplicate_pending_fence_blocks_single_target_background_review_and_allows_foreground(
    admission_env: dict[str, Path],
) -> None:
    from utils import atomic_json_write
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    _make_skill(admission_env["create_dir"], "openspec-apply-change")
    atomic_json_write(
        wa._pending_path(wa.SKILLS, "abc12345"),
        {
            "id": "abc12345",
            "subsystem": "skills",
            "action": "patch",
            "summary": "existing patch",
            "origin": "background_review",
            "created_at": 1000.0,
            "payload": {
                "action": "patch",
                "name": "openspec-apply-change",
                "old_string": "Initial rule.",
                "new_string": "Pending v1.",
            },
        },
    )

    with _as_background_review():
        flat_dup = json.loads(
            smt.skill_manage(
                action="patch",
                name="openspec-apply-change",
                old_string="Initial rule.",
                new_string="Pending v2.",
            )
        )
        batch_dup = json.loads(
            smt.skill_manage(
                action="",
                name="",
                operations=[
                    {
                        "action": "patch",
                        "name": "openspec-apply-change",
                        "old_string": "Initial rule.",
                        "new_string": "Pending v3.",
                    }
                ],
            )
        )

    for dup in (flat_dup, batch_dup):
        assert dup["success"] is False
        assert dup["duplicate_pending"] is True
        assert dup["existing_pending_ids"] == ["abc12345"]
    assert wa.pending_count(wa.SKILLS) == 1

    # Foreground user-directed proposal is NEVER blocked by the duplicate fence:
    fg_res = json.loads(
        smt.skill_manage(
            action="patch",
            name="openspec-apply-change",
            old_string="Initial rule.",
            new_string="Explicit user patch.",
        )
    )
    assert fg_res["success"] is True
    assert fg_res["staged"] is True
    assert wa.pending_count(wa.SKILLS) == 2


def test_split_cross_skill_batch_skips_duplicate_targets_and_fails_when_all_duplicate(
    admission_env: dict[str, Path],
) -> None:
    from utils import atomic_json_write
    from tools import skill_manager_tool as smt
    from tools import write_approval as wa

    _make_skill(admission_env["local_dir"], "alpha")
    _make_skill(admission_env["local_dir"], "beta")
    atomic_json_write(
        wa._pending_path(wa.SKILLS, "aaa00001"),
        {
            "id": "aaa00001",
            "subsystem": "skills",
            "action": "patch",
            "summary": "existing alpha patch",
            "origin": "background_review",
            "created_at": 1000.0,
            "payload": {
                "action": "patch",
                "name": "alpha",
                "old_string": "Initial rule.",
                "new_string": "Alpha v1.",
            },
        },
    )

    with _as_background_review():
        partial = json.loads(
            smt.skill_manage(
                action="",
                name="",
                operations=[
                    {"action": "patch", "name": "alpha", "old_string": "Initial rule.", "new_string": "Alpha v2."},
                    {"action": "patch", "name": "beta", "old_string": "Initial rule.", "new_string": "Beta v1."},
                ],
            )
        )
        assert partial["success"] is True
        assert partial["staged"] is True
        assert len(partial["pending_ids"]) == 1
        assert partial["split_targets"] == ["beta"]
        assert partial["skipped_duplicates"] == {"alpha": ["aaa00001"]}
        assert wa.pending_count(wa.SKILLS) == 2

        all_dup = json.loads(
            smt.skill_manage(
                action="",
                name="",
                operations=[
                    {"action": "patch", "name": "alpha", "old_string": "Initial rule.", "new_string": "Alpha v3."},
                    {"action": "patch", "name": "beta", "old_string": "Initial rule.", "new_string": "Beta v2."},
                ],
            )
        )
        assert all_dup["success"] is False
        assert all_dup["duplicate_pending"] is True
        assert set(all_dup["skipped_duplicates"].keys()) == {"alpha", "beta"}
        assert wa.pending_count(wa.SKILLS) == 2


# ---------------------------------------------------------------------------
# Task 1.4: Background Review Prompt Alignment with Admission Boundaries
# ---------------------------------------------------------------------------


def test_review_prompts_and_spawned_advisory_align_with_admission_guards(
    admission_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import agent.background_review as br
    from tools import write_approval as wa

    for base_prompt in (br._SKILL_REVIEW_PROMPT, br._COMBINED_REVIEW_PROMPT):
        assert "read_only_dirs" in base_prompt
        assert "one skill per" in base_prompt.lower() or "single skill" in base_prompt.lower()

    wa.stage_write(
        wa.SKILLS,
        {"action": "patch", "name": "openspec-apply-change", "old_string": "a", "new_string": "b"},
        summary="patch openspec",
        origin="background_review",
    )
    wa.stage_write(
        wa.SKILLS,
        {"action": "patch", "name": "ask-agent-at", "old_string": "a", "new_string": "b"},
        summary="patch ask-agent-at",
        origin="background_review",
    )

    dummy_agent = SimpleNamespace()
    _target, skill_prompt = br.spawn_background_review_thread(
        dummy_agent,
        messages_snapshot=[],
        review_memory=False,
        review_skills=True,
        task_cfg={},
    )
    assert "read_only_dirs" in skill_prompt
    assert "Skills already pending review" in skill_prompt
    assert "ask-agent-at, openspec-apply-change" in skill_prompt

    # Memory-only review does NOT inject pending skill hints:
    _mem_target, mem_prompt = br.spawn_background_review_thread(
        dummy_agent,
        messages_snapshot=[],
        review_memory=True,
        review_skills=False,
        task_cfg={},
    )
    assert "Skills already pending review" not in mem_prompt

    # Bounded advisory cap (max 50 names) + fail-open when pending store raises:
    monkeypatch.setattr(
        wa,
        "pending_skill_targets",
        lambda subsystem=wa.SKILLS: {f"skill-{i:03d}": [f"id{i}"] for i in range(60)},
    )
    _cap_target, capped_prompt = br.spawn_background_review_thread(
        dummy_agent,
        messages_snapshot=[],
        review_memory=True,
        review_skills=True,
        task_cfg={},
    )
    assert "skill-000" in capped_prompt
    assert "skill-049" in capped_prompt
    assert "skill-050" not in capped_prompt

    def _boom(*_a, **_kw):
        raise OSError("pending dir unreadable")

    monkeypatch.setattr(wa, "pending_skill_targets", _boom)
    _err_target, fallback_prompt = br.spawn_background_review_thread(
        dummy_agent,
        messages_snapshot=[],
        review_memory=False,
        review_skills=True,
        task_cfg={},
    )
    assert fallback_prompt == br._SKILL_REVIEW_PROMPT
