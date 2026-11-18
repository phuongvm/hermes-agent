"""Saved catalog validation reports bounded reasons, without opening resources."""
from pathlib import Path

import pytest

from agent.prerequisite_catalog import catalog_diagnostics
from tests.tools.proposal_catalog_fixture import profile_config, proposal_entry


def _duplicate_identity(rows):
    rows["governance/shared/safety.md"].update(
        {key: rows["governance/AGENTS.md"][key] for key in ("device", "inode")},
    )


@pytest.mark.parametrize("modify,reason", [
    pytest.param(lambda rows, root: None, None, id="complete"),
    pytest.param(lambda rows, root: rows.pop("reference/verification-before-claim/references/completion-gate.md"),
                 "incomplete-resource-set", id="missing-reference"),
    pytest.param(lambda rows, root: rows["project/agent-share"].update(path=str(root / "must-not-echo.md")),
                 "relative-project-path-required", id="invalid-path"),
    pytest.param(lambda rows, root: rows["governance/AGENTS.md"].update(sha256="must-not-echo"),
                 "invalid-digest", id="invalid-digest"),
    pytest.param(lambda rows, root: rows["governance/shared/safety.md"].update(path=str(root / "other/shared/safety.md")),
                 "unexpected-governance-resource", id="governance-substitution"),
    pytest.param(lambda rows, root: rows["skill/deep-reference-protocol"].update(path=str(root / "wrong-skill/SKILL.md")),
                 "unexpected-skill-resource", id="skill-substitution"),
    pytest.param(lambda rows, root: _duplicate_identity(rows), "duplicate-resource-identity", id="duplicate-identity"),
    pytest.param(lambda rows, root: rows["governance/AGENTS.md"].update(path=r"/\fixture-host/share/AGENTS.md"),
                 "unsupported-path", id="mixed-unc"),
    pytest.param(lambda rows, root: rows["governance/AGENTS.md"].update(path=r"\??\C:\fixture\AGENTS.md"),
                 "unsupported-path", id="nt-namespace"),
    pytest.param(lambda rows, root: rows["governance/AGENTS.md"].update(path=str(root / "NUL/AGENTS.md")),
                 "unsupported-path", id="reserved-device", marks=pytest.mark.platforms("windows")),
])
def test_catalog_diagnostics_do_not_open_resources(modify, reason, tmp_path, monkeypatch):
    entry = proposal_entry(tmp_path / "planning")
    modify(entry["resources"], tmp_path)
    catalog = profile_config(entry)["prerequisite_catalog"]

    def unexpected_open(*args, **kwargs):
        pytest.fail("configuration checks must not open resource contents")

    with monkeypatch.context() as scope:
        scope.setattr(Path, "open", unexpected_open)
        assert catalog_diagnostics(catalog) == ([reason] if reason else [])


def test_catalog_diagnostics_reject_unknown_workflow(tmp_path):
    catalog = profile_config(proposal_entry(tmp_path))["prerequisite_catalog"]
    catalog["must-not-echo"] = {}
    assert catalog_diagnostics(catalog) == ["invalid-workflow-map"]


@pytest.mark.parametrize("key", [
    "snapshot/status", "snapshot/instructions/proposal", "snapshot/instructions/design",
    "snapshot/instructions/specs", "snapshot/instructions/tasks", "project/requirements",
    "project/architecture", "reference/openspec-workflow-governance/references/openspec-agent-workflow-guide.md",
    "skill/hermes-boot-protocol", "governance/workflow/plan-execution-standards.md",
    "governance/guides/ai-native-documentation.md",
])
def test_catalog_rejects_incomplete_proposal_declaration(key, tmp_path):
    from agent.prerequisite_catalog import InvalidCatalog, freeze_proposal_catalog
    from tests.tools.proposal_catalog_fixture import CHANGE

    entry = proposal_entry(tmp_path / "planning")
    entry["resources"].pop(key, None)
    with pytest.raises(InvalidCatalog, match="^incomplete-resource-set$"):
        freeze_proposal_catalog(CHANGE, entry)


@pytest.mark.parametrize("invalid", ["policy: [", "[non-mapping]"])
def test_catalog_selection_rejects_invalid_managed_layer_after_nonstrict_read(tmp_path, monkeypatch, caplog, invalid):
    from json import dumps

    from agent.prerequisite_workflow import _selection
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tests.tools.proposal_catalog_fixture import CHANGE

    home, managed = tmp_path / "home", tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    config = profile_config(proposal_entry(tmp_path / "planning"))
    (home / "config.yaml").write_text(dumps(config), encoding="utf-8")
    (managed / "config.yaml").write_text(invalid, encoding="utf-8")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    caplog.set_level("INFO", logger="agent.prerequisite_workflow")
    token = set_hermes_home_override(str(home))
    try:
        assert load_user_config_effective()["prerequisite_catalog"] == config["prerequisite_catalog"]
        caplog.clear()
        assert _selection(CHANGE) is None
        assert caplog.messages == ["PREREQUISITE_SELECTION_UNAVAILABLE: profile-config-unavailable"]
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize("config,reason", [
    ({}, "catalog-absent"),
    ({"prerequisite_catalog": {}}, "catalog-absent"),
    ({"prerequisite_catalog": None}, "invalid-workflow-map"),
    ({"prerequisite_catalog": {"openspec-propose": {"other-change": {}}}}, "change-not-configured"),
])
def test_catalog_selection_reports_distinct_bounded_reasons(monkeypatch, caplog, config, reason):
    from agent.prerequisite_workflow import _selection
    from tests.tools.proposal_catalog_fixture import CHANGE

    monkeypatch.setattr("hermes_cli.config_effective.load_user_config_effective", lambda **_: config)
    caplog.set_level("INFO", logger="agent.prerequisite_workflow")
    assert _selection(CHANGE) is None
    assert caplog.messages == [f"PREREQUISITE_SELECTION_UNAVAILABLE: {reason}"]


@pytest.mark.parametrize("managed_value,reason", [
    ({"openspec-propose": {"example-topic": {"resources": {"project/requirements": None}}}}, None),
    (None, "invalid-workflow-map"),
    ({"openspec-propose": {"example-topic": {"resources": {"project/requirements": {"size": 1}}}}},
     "invalid-resource"),
])
def test_managed_catalog_preserves_explicit_absence(tmp_path, monkeypatch, caplog, managed_value, reason):
    from json import dumps

    from agent.prerequisite_workflow import _selection
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tests.tools.proposal_catalog_fixture import CHANGE, resource_pin

    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    entry["resources"]["project/requirements"] = resource_pin(planning, "REQUIREMENTS.md")
    user, managed = tmp_path / "user", tmp_path / "managed"
    user.mkdir()
    managed.mkdir()
    user_config = {**profile_config(entry), "display": {"skin": "fixture"}}
    managed_config = {"prerequisite_catalog": managed_value, "display": None}
    (user / "config.yaml").write_text(dumps(user_config), encoding="utf-8")
    (managed / "config.yaml").write_text(dumps(managed_config), encoding="utf-8")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    caplog.set_level("INFO", logger="agent.prerequisite_workflow")
    token = set_hermes_home_override(str(user))
    try:
        from hermes_cli.config_effective import load_user_config_effective

        assert load_user_config_effective(fail_closed=True, strict_managed=True)["display"] == user_config["display"]
        selection = _selection(CHANGE)
        if reason:
            assert selection is None
            assert caplog.messages == [f"PREREQUISITE_SELECTION_UNAVAILABLE: {reason}"]
        else:
            assert selection is not None
            assert "project/requirements" in selection.absent_optional
            assert all(pin.key != "project/requirements" for pin in selection.resources)
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize("path", ["docs/REQUIREMENTS.md", "design/ARCHITECTURE.md"])
def test_catalog_accepts_explicit_nested_optional_document(tmp_path, path):
    from agent.prerequisite_catalog import freeze_proposal_catalog
    from tests.tools.proposal_catalog_fixture import CHANGE, resource_pin

    entry = proposal_entry(tmp_path)
    key = "project/" + Path(path).stem.lower()
    entry["resources"][key] = resource_pin(tmp_path, path)
    selection = freeze_proposal_catalog(CHANGE, entry)
    assert next(pin.path for pin in selection.resources if pin.key == key) == str(tmp_path / path)


@pytest.mark.parametrize("path,reason", [
    ("../REQUIREMENTS.md", "unsupported-path"),
    ("/REQUIREMENTS.md", "relative-project-path-required"),
])
def test_catalog_rejects_optional_documents_outside_planning_home(tmp_path, path, reason):
    from agent.prerequisite_catalog import InvalidCatalog, freeze_proposal_catalog
    from tests.tools.proposal_catalog_fixture import CHANGE, resource_pin

    entry = proposal_entry(tmp_path)
    pin = resource_pin(tmp_path, "REQUIREMENTS.md")
    entry["resources"]["project/requirements"] = {**pin, "path": path}
    with pytest.raises(InvalidCatalog, match=f"^{reason}$"):
        freeze_proposal_catalog(CHANGE, entry)


@pytest.mark.platforms("windows")
def test_catalog_rejects_unassigned_drive_before_metadata_io(tmp_path, monkeypatch):
    from ctypes import windll
    from dataclasses import replace
    from string import ascii_uppercase

    from agent.prerequisite_catalog import InvalidCatalog, freeze_proposal_catalog, verify_catalog_metadata
    from tests.tools.proposal_catalog_fixture import CHANGE

    mask = windll.kernel32.GetLogicalDrives()
    assert mask
    unused = next((letter for index, letter in enumerate(ascii_uppercase) if not mask & (1 << index)), None)
    if unused is None:
        pytest.skip("fixture requires an unassigned drive letter")
    selection = freeze_proposal_catalog(CHANGE, proposal_entry(tmp_path / "planning"))
    rejected = replace(selection.resources[0], path=f"{unused}:/unused/fixture.md")

    def unexpected_stat(*args, **kwargs):
        pytest.fail("volume rejection must precede resource and ancestor metadata access")

    with monkeypatch.context() as scope:
        scope.setattr(type(tmp_path), "lstat", unexpected_stat)
        with pytest.raises(InvalidCatalog, match="^nonlocal-volume$"):
            verify_catalog_metadata(replace(selection, resources=(rejected,)))
