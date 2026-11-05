"""Authoritative staged Layer 0 gate for pending skill writes (BLOCKED_LAYER0_EVAL).

Spec: openspec/changes/hermes-skill-evaluation-and-admission-governance/specs/pending-skill-governance/spec.md

Every blocked scenario asserts the fail-closed contract AND byte-for-byte immutability (SHA-256 before/after) of the
live target skill directory and of the pending ``<id>.json`` queue file.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

SKILL_NAME = "layer0-demo"
GOOD_SKILL = textwrap.dedent(
    """\
    ---
    name: layer0-demo
    description: Verify layer zero demo skill admission
    ---
    # Layer 0 demo

    ## When to Use
    Use when verifying the staged gate.
    """
)
BAD_DESC_SKILL = GOOD_SKILL.replace(
    "description: Verify layer zero demo skill admission",
    "description: " + "Verify an extremely long description that blows the sixty character budget " * 2,
)


def _verdict(**over):
    base = dict(
        skill_name=SKILL_NAME, skill_dir="staged", tier=1, passed=True, effective_desc_length=40, max_desc_budget=60,
        front_loaded_trigger_ok=True, linter_errors=(), guard_verdict="safe", guard_findings=(),
        tier1_static_findings=(), shadow_status="active", nerve_admission_recall=1.0, provider_calls=0,
    )
    base.update(over)
    return base


@pytest.fixture
def home(monkeypatch):
    d = tempfile.mkdtemp(prefix="hermes_l0_test_")
    root = os.path.join(d, ".hermes")
    os.makedirs(os.path.join(root, "skills"))
    monkeypatch.setenv("HERMES_HOME", root)
    yield Path(root)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def wac():
    import hermes_cli.write_approval_commands as module

    return module


def _stage(payload, summary="demo"):
    from tools import write_approval as wa

    return wa.stage_write(wa.SKILLS, payload, summary=summary, origin="foreground")


def _create_payload(content=GOOD_SKILL):
    return {"action": "create", "name": SKILL_NAME, "content": content}


def _live_skill(home, content=GOOD_SKILL):
    d = home / "skills" / SKILL_NAME
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d


def _tree_sha(path: Path) -> str:
    h = hashlib.sha256()
    if not path.exists():
        return "absent"
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(f.relative_to(path).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def _pending_json(home, rec) -> Path:
    return home / "pending" / "skills" / f"{rec['id']}.json"


def _snapshot(home, rec):
    live = home / "skills" / SKILL_NAME
    return {"target": _tree_sha(live), "queue": _file_sha(_pending_json(home, rec)), "queue_listing": sorted(
        p.name for p in (home / "pending" / "skills").glob("*"))}


def _assert_blocked(res, *, error_expected):
    assert res["effective_status"] == "BLOCKED_LAYER0_EVAL"
    assert res["governance_verdict"] == "REVISE"
    assert res["native_disposition"] == "NOT_ATTEMPTED"
    assert res.get("replayed", False) is False
    assert res.get("discarded", False) is False
    if error_expected:
        assert res["layer0_verdict"] is None and isinstance(res["layer0_error"], str) and res["layer0_error"]
    else:
        assert res["layer0_verdict"] is not None and res["layer0_error"] is None


def _install_eval(monkeypatch, wac, fn, seen=None):
    def runner(skill_dir, timeout_s=5.0):
        if seen is not None:
            seen.append(Path(skill_dir))
        return fn(Path(skill_dir))

    monkeypatch.setattr(wac, "_run_layer0_eval", runner)


FAILING_VERDICTS = {
    "description_over_budget": _verdict(passed=False, effective_desc_length=140, max_desc_budget=60),
    "dangling_reference": _verdict(passed=False, linter_errors=("dangling-reference: body references 'references/x.md'",)),
    "skills_guard_dangerous": _verdict(passed=False, guard_verdict="dangerous", guard_findings=("curl_pipe_shell:SKILL.md:3",)),
    "nerve_recall_below_80": _verdict(passed=False, nerve_admission_recall=0.5),
    "name_dir_mismatch": _verdict(passed=False, linter_errors=("name-dir-mismatch: name 'x' != dir",)),
    "shadow_ambiguous": _verdict(passed=False, shadow_status="ambiguous"),
    "no_front_loaded_trigger": _verdict(passed=False, front_loaded_trigger_ok=False),
    # a lying evaluator: passed=True but the hard criteria are violated -> re-checked, still blocked
    "passed_flag_lies": _verdict(passed=True, effective_desc_length=99, max_desc_budget=60),
}


@pytest.mark.parametrize("name", sorted(FAILING_VERDICTS))
def test_preflight_failed_verdict_blocks_create_and_leaves_queue_untouched(home, wac, monkeypatch, name):
    rec = _stage(_create_payload())
    _install_eval(monkeypatch, wac, lambda d: FAILING_VERDICTS[name])
    before = _snapshot(home, rec)

    result = wac.preflight_pending_skill_review()

    item = result["records"][0]
    _assert_blocked(item, error_expected=False)
    assert item.get("layer0_passed") is False
    assert result["records_unchanged"] is True
    assert _snapshot(home, rec) == before
    assert not (home / "skills" / SKILL_NAME).exists()


@pytest.mark.parametrize("name", sorted(FAILING_VERDICTS))
def test_approve_failed_verdict_does_not_replay_or_discard(home, wac, monkeypatch, name):
    rec = _stage(_create_payload())
    _install_eval(monkeypatch, wac, lambda d: FAILING_VERDICTS[name])
    before = _snapshot(home, rec)

    res = wac.approve_pending_native("skills", rec["id"])

    assert res["success"] is False and res["subsystem"] == "skills" and res["pending_id"] == rec["id"]
    assert res["target"] == SKILL_NAME
    _assert_blocked(res, error_expected=False)
    assert res["error"]
    assert _snapshot(home, rec) == before


@pytest.mark.parametrize(
    "exc",
    [ImportError("gate module missing"), TimeoutError("exceeded 5.0s"), OSError("disk full"), ValueError("bad"),
     TypeError("bad"), RuntimeError("boom")],
    ids=lambda e: type(e).__name__,
)
def test_evaluator_exception_fails_closed_with_identical_target_and_queue(home, wac, monkeypatch, exc):
    live = _live_skill(home)
    rec = _stage({"action": "patch", "name": SKILL_NAME, "old_string": "Layer 0 demo", "new_string": "Layer 0 demo v2"})

    def boom(_dir):
        raise exc

    _install_eval(monkeypatch, wac, boom)
    before = _snapshot(home, rec)

    pre = wac.preflight_pending_skill_review()["records"][0]
    _assert_blocked(pre, error_expected=True)
    assert type(exc).__name__ in pre["layer0_error"]

    res = wac.approve_pending_native("skills", rec["id"])
    assert res["success"] is False
    _assert_blocked(res, error_expected=True)

    cons = wac.consolidate_pending_native("skills", [rec["id"]])
    assert cons["success"] is False and cons["native_disposition"] == "FAILED"
    _assert_blocked(cons["results"][0], error_expected=True)

    assert _snapshot(home, rec) == before
    assert (live / "SKILL.md").read_text(encoding="utf-8") == GOOD_SKILL


@pytest.mark.parametrize(
    "bad",
    [None, "ok", 42, {}, _verdict(passed="yes"), _verdict(provider_calls=3), _verdict(guard_verdict="weird"),
     _verdict(shadow_status="maybe"), _verdict(nerve_admission_recall=1.7), _verdict(linter_errors=[1]),
     {k: v for k, v in _verdict().items() if k != "front_loaded_trigger_ok"}],
    ids=lambda v: "malformed",
)
def test_malformed_verdict_fails_closed(home, wac, monkeypatch, bad):
    rec = _stage(_create_payload())
    _install_eval(monkeypatch, wac, lambda d: bad)
    before = _snapshot(home, rec)

    res = wac.approve_pending_native("skills", rec["id"])

    assert res["success"] is False
    _assert_blocked(res, error_expected=True)
    assert _snapshot(home, rec) == before
    assert not (home / "skills" / SKILL_NAME).exists()


def test_gate_module_import_failure_fails_closed(home, wac, monkeypatch):
    rec = _stage(_create_payload())
    monkeypatch.setitem(sys.modules, "hermes_cli.pending_skill_layer0", None)  # makes the import raise ImportError
    before = _snapshot(home, rec)

    res = wac.approve_pending_native("skills", rec["id"])

    _assert_blocked(res, error_expected=True)
    assert "ModuleNotFoundError" in res["layer0_error"] or "ImportError" in res["layer0_error"]
    assert _snapshot(home, rec) == before


def test_valid_pending_create_is_allowed_and_replays_cleanly(home, wac, monkeypatch):
    rec = _stage(_create_payload())
    seen = []
    _install_eval(monkeypatch, wac, lambda d: _verdict(), seen)

    item = wac.preflight_pending_skill_review()["records"][0]
    assert item["effective_status"] == "APPROVE" and item["governance_verdict"] == "APPROVE"
    assert item["layer0_passed"] is True and item["native_disposition"] == "NOT_ATTEMPTED"
    assert not (home / "skills" / SKILL_NAME).exists()  # preflight never writes

    res = wac.approve_pending_native("skills", rec["id"])

    assert res["success"] is True and res["replayed"] is True and res["discarded"] is True
    assert (home / "skills" / SKILL_NAME / "SKILL.md").read_text(encoding="utf-8") == GOOD_SKILL
    assert not _pending_json(home, rec).exists()
    assert seen, "the Layer 0 evaluator must have been consulted"


def test_staged_candidate_not_live_dir_is_evaluated_and_staging_is_cleaned(home, wac, monkeypatch):
    live = _live_skill(home)
    rec = _stage({"action": "patch", "name": SKILL_NAME, "old_string": "Layer 0 demo", "new_string": "Layer 0 demo PATCHED"})
    seen, contents = [], []

    def spy(d):
        contents.append((d / "SKILL.md").read_text(encoding="utf-8"))
        return _verdict(passed=False, effective_desc_length=99)

    _install_eval(monkeypatch, wac, spy, seen)
    before = _snapshot(home, rec)

    res = wac.approve_pending_native("skills", rec["id"])

    _assert_blocked(res, error_expected=False)
    assert seen and all(Path(p).resolve() != live.resolve() for p in seen)
    assert all("hermes-layer0-stage-" in str(p) for p in seen)
    assert "PATCHED" in contents[0]
    assert all(not Path(p).exists() for p in seen), "temporary staging directory must be removed"
    assert _snapshot(home, rec) == before
    assert "PATCHED" not in (live / "SKILL.md").read_text(encoding="utf-8")


def test_write_file_and_remove_file_and_batch_are_staged(home, wac, monkeypatch):
    live = _live_skill(home)
    (live / "references").mkdir()
    (live / "references" / "old.md").write_text("old", encoding="utf-8")
    ops = [
        {"action": "write_file", "name": SKILL_NAME, "file_path": "references/new.md", "file_content": "new"},
        {"action": "remove_file", "name": SKILL_NAME, "file_path": "references/old.md"},
        {"action": "patch", "name": SKILL_NAME, "old_string": "Use when", "new_string": "Use whenever"},
    ]
    rec = _stage({"action": "batch", "name": SKILL_NAME, "operations": ops})
    shapes = []

    def spy(d):
        shapes.append({"new": (d / "references" / "new.md").exists(), "old": (d / "references" / "old.md").exists(),
                       "md": (d / "SKILL.md").read_text(encoding="utf-8")})
        return _verdict(passed=False, effective_desc_length=99)

    _install_eval(monkeypatch, wac, spy)
    before = _snapshot(home, rec)

    res = wac.approve_pending_native("skills", rec["id"])

    _assert_blocked(res, error_expected=False)
    assert shapes[0]["new"] is True and shapes[0]["old"] is False and "Use whenever" in shapes[0]["md"]
    assert _snapshot(home, rec) == before


def test_unappliable_pending_payload_fails_closed(home, wac, monkeypatch):
    _live_skill(home)
    rec = _stage({"action": "patch", "name": SKILL_NAME, "old_string": "THIS TEXT IS NOT THERE", "new_string": "x"})
    _install_eval(monkeypatch, wac, lambda d: _verdict())
    before = _snapshot(home, rec)

    res = wac.approve_pending_native("skills", rec["id"])

    _assert_blocked(res, error_expected=True)
    assert _snapshot(home, rec) == before


def test_path_traversal_in_pending_write_file_is_rejected_in_staging(home, wac, monkeypatch):
    _live_skill(home)
    rec = _stage({"action": "write_file", "name": SKILL_NAME, "file_path": "../../escape.md", "file_content": "x"})
    _install_eval(monkeypatch, wac, lambda d: _verdict())

    res = wac.approve_pending_native("skills", rec["id"])

    _assert_blocked(res, error_expected=True)
    assert not (home / "escape.md").exists() and not (home / "skills" / "escape.md").exists()


def test_effective_status_is_a_valid_governance_enum(wac):
    assert "BLOCKED_LAYER0_EVAL" in wac.EFFECTIVE_STATUSES
    result = {
        "governance_verdict": "REVISE", "native_disposition": "NOT_ATTEMPTED", "effective_status": "BLOCKED_LAYER0_EVAL",
        "execution_status": "COMPLETED", "delivery_status": "NOT_ATTEMPTED", "pending_ids_before": [],
        "pending_ids_after": [], "native_results": [], "target_read_back": [],
    }
    assert wac.validate_pending_governance_result(result) is result


def test_memory_subsystem_is_not_gated(home, wac, monkeypatch):
    from tools import write_approval as wa

    calls = []
    _install_eval(monkeypatch, wac, lambda d: calls.append(d))
    rec = wa.stage_write(wa.MEMORY, {"action": "add", "target": "user", "content": "x"}, summary="m", origin="foreground")
    res = wac.reject_pending_native("memory", rec["id"])
    assert res["success"] is True and calls == []


def test_reject_is_not_gated_by_layer0(home, wac, monkeypatch):
    rec = _stage(_create_payload())
    _install_eval(monkeypatch, wac, lambda d: pytest.fail("reject must not evaluate"))
    res = wac.reject_pending_native("skills", rec["id"])
    assert res["success"] is True and res["discarded"] is True and res["replayed"] is False


# ----------------------------------------------------------------------------------------------- real runner
GATE_STUB = textwrap.dedent(
    """\
    import time, dataclasses
    @dataclasses.dataclass
    class V:
        passed: bool = True
    def evaluate_skill_layer0(skill_dir, evals_path=None, hermes_home=None):
        time.sleep(30)
    """
)


def test_real_runner_timeout_is_a_hard_timeout(tmp_path, monkeypatch):
    from hermes_cli import pending_skill_layer0 as mod

    stub = tmp_path / "gate_stub.py"
    stub.write_text(GATE_STUB, encoding="utf-8")
    monkeypatch.setenv(mod.LAYER0_GATE_ENV, str(stub))
    with pytest.raises(TimeoutError):
        mod.run_layer0_eval(tmp_path, timeout_s=1.0)


def test_real_runner_missing_gate_raises_importerror(tmp_path, monkeypatch):
    from hermes_cli import pending_skill_layer0 as mod

    monkeypatch.setenv(mod.LAYER0_GATE_ENV, str(tmp_path / "nope.py"))
    with pytest.raises(ImportError):
        mod.run_layer0_eval(tmp_path)


def test_real_gate_timeout_blocks_through_public_approve(home, wac, monkeypatch, tmp_path):
    from hermes_cli import pending_skill_layer0 as mod

    stub = tmp_path / "gate_stub.py"
    stub.write_text(GATE_STUB, encoding="utf-8")
    monkeypatch.setenv(mod.LAYER0_GATE_ENV, str(stub))
    rec = _stage(_create_payload())
    before = _snapshot(home, rec)
    res = mod.evaluate_pending_skill_layer0_staged(rec, timeout_s=1.0)
    assert res["effective_status"] == "BLOCKED_LAYER0_EVAL" and "TimeoutError" in res["layer0_error"]
    assert _snapshot(home, rec) == before


GATE_PATH = Path("O:/workspaces/ai_agents/hermes-agent/scripts/skill_eval/hermes_skill_eval_gate.py")


@pytest.mark.skipif(not GATE_PATH.is_file(), reason="Layer 0 gate script not present on this host")
def test_real_layer0_gate_blocks_overlong_description_and_admits_valid_skill(home, wac, monkeypatch):
    """End-to-end with the genuine evaluate_skill_layer0 (isolated interpreter, 5.0s budget)."""
    monkeypatch.setenv("HERMES_LAYER0_PYTHON", sys.executable)
    bad = _stage(_create_payload(BAD_DESC_SKILL))
    before = _snapshot(home, bad)
    res = wac.approve_pending_native("skills", bad["id"])
    _assert_blocked(res, error_expected=False)
    assert res["layer0_verdict"]["passed"] is False and res["layer0_verdict"]["effective_desc_length"] > 60
    assert _snapshot(home, bad) == before

    from tools import write_approval as wa

    wa.discard_pending(wa.SKILLS, bad["id"])
    good = _stage(_create_payload(GOOD_SKILL))
    item = wac.preflight_pending_skill_review()["records"][0]
    assert item["effective_status"] == "APPROVE" and item["layer0_passed"] is True, item
    ok = wac.approve_pending_native("skills", good["id"])
    assert ok["success"] is True and ok["replayed"] is True
