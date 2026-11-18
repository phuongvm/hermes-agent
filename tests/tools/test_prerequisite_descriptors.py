"""Metadata resolution only: matching does not dispatch or read content."""
from dataclasses import replace

import pytest

from agent.delegation_context import native_task_intent
from agent.prerequisite.catalog import freeze_proposal_catalog
from tests.tools.proposal_catalog_fixture import CHANGE, proposal_entry


@pytest.fixture
def assignment(tmp_path, monkeypatch):
    import agent.delegation_context as delegation

    root = tmp_path / "planning"
    selection = freeze_proposal_catalog(CHANGE, proposal_entry(root))
    monkeypatch.setattr(delegation, "child_proposal_selection", lambda *a, **kw: selection)
    with native_task_intent(actor_session_id="actor", parent_session_id="parent", goal="Write proposal.md",
                            context=None) as intent:
        yield intent, root


def test_matches_only_frozen_operations(assignment):
    from agent.prerequisite.descriptors import resolve_operation

    intent, root = assignment
    workdir = str(root)
    cases = [
        ("read_file", {"path": str(root / "rules/AGENTS.md")}, ("governance/AGENTS.md",)),
        ("skill_view", {"name": "verification-before-claim", "file_path": "references/completion-gate.md"},
         ("skill/verification-before-claim", "reference/verification-before-claim/references/completion-gate.md")),
        ("openspec_status", {"workdir": workdir, "change": CHANGE}, ("snapshot/status",)),
        ("openspec_instructions", {"workdir": workdir, "change": CHANGE, "artifact": "tasks"},
         ("snapshot/instructions/tasks",)),
        ("openspec_spec_show", {"workdir": workdir, "spec": "nerve-reflex-resilience"},
         ("baseline/nerve-reflex-resilience",)),
        ("openspec_spec_list", {"workdir": workdir},
         ("baseline/nerve-crew-dod-and-role-handoff", "baseline/nerve-reflex-resilience")),
    ]
    for tool, args, keys in cases:
        result = resolve_operation(intent, "actor", tool, args)
        assert result is not None
        assert result.tool == tool
        assert tuple(pin.key for pin in result.resources) == keys
    read = resolve_operation(intent, "actor", *cases[0][:2])
    assert dict(read.parameters) == {"path": str(root / "rules/AGENTS.md"), "offset": 1, "limit": 2000}


@pytest.mark.parametrize("tool,args", [
    ("terminal", {}), ("execute_code", {}), ("web_extract", {}), ("search_files", {}), ("tool_call", {}),
    ("read_file", {"offset": True}), ("read_file", {"limit": 2001}), ("read_file", {"offset": 1048578}),
    ("read_file", {"encoding": "utf-8"}), ("read_file", {"path": "rules/AGENTS.md"}),
    ("skill_view", {"name": "verification-before-claim", "file_path": "../SKILL.md"}),
    ("openspec_status", {"change": "other-topic"}), ("openspec_status", {"schema": "custom"}),
    ("openspec_instructions", {"artifact": "apply"}), ("openspec_spec_show", {"spec": "*"}),
    ("openspec_spec_list", {"workdir": "other"}), ("openspec_spec_list", {"schema": "spec-driven"}),
])
def test_non_declared_operation_has_no_descriptor(assignment, tool, args):
    from agent.prerequisite.descriptors import resolve_operation

    intent, root = assignment
    defaults = {"read_file": {"path": str(root / "rules/AGENTS.md")},
                "openspec_status": {"workdir": str(root), "change": CHANGE},
                "openspec_instructions": {"workdir": str(root), "change": CHANGE},
                "openspec_spec_show": {"workdir": str(root)}, "openspec_spec_list": {"workdir": str(root)}}
    assert resolve_operation(intent, "actor", tool, {**defaults.get(tool, {}), **args}) is None


def test_manifest_and_active_assignment_are_required(assignment):
    from agent.prerequisite.descriptors import resolve_operation

    intent, root = assignment
    args = {"path": str(root / "rules/AGENTS.md")}
    assert resolve_operation(None, "actor", "read_file", args) is None
    assert resolve_operation(replace(intent), "actor", "read_file", args) is None
    assert resolve_operation(intent, "other-actor", "read_file", args) is None
    with native_task_intent(actor_session_id="other", parent_session_id="parent", goal="Write proposal.md",
                            context=None, resumed=True) as unbound:
        assert unbound is None
        assert resolve_operation(intent, "actor", "read_file", args) is None


def test_stale_or_oversize_resource_has_no_descriptor(assignment):
    from agent.prerequisite.descriptors import resolve_operation

    intent, root = assignment
    path = root / "rules/AGENTS.md"
    path.write_bytes(b"x" * 513)
    assert resolve_operation(intent, "actor", "read_file", {"path": str(path)}) is None
