"""Saved catalog validation reports bounded reasons, without opening resources."""
from pathlib import Path

import pytest

from hermes_cli.config_check_diagnostics import config_check_diagnostics
from tests.tools.proposal_catalog_fixture import profile_config, proposal_entry


@pytest.mark.parametrize("case,reason", [
    ("complete", None),
    ("missing-reference", "incomplete-resource-set"),
    ("invalid-path", "relative-project-path-required"),
    ("invalid-digest", "invalid-digest"),
    ("unsupported-workflow", "invalid-workflow-map"),
    ("governance-substitution", "unexpected-governance-resource"),
    ("skill-substitution", "unexpected-skill-resource"),
    ("duplicate-identity", "duplicate-resource-identity"),
    ("mixed-unc", "unsupported-path"), ("nt-namespace", "unsupported-path"),
    pytest.param("reserved-device", "unsupported-path", marks=pytest.mark.platforms("windows")),
])
def test_config_check_diagnoses_prerequisite_catalog(case, reason, tmp_path, monkeypatch):
    entry = proposal_entry(tmp_path / "planning")
    config = profile_config(entry)
    rows = entry["resources"]
    if case == "missing-reference":
        del rows["reference/verification-before-claim/references/completion-gate.md"]
    elif case == "invalid-path":
        rows["project/agent-share"]["path"] = str(tmp_path / "must-not-echo.md")
    elif case == "invalid-digest":
        rows["governance/AGENTS.md"]["sha256"] = "must-not-echo"
    elif case == "unsupported-workflow":
        config["prerequisite_catalog"]["must-not-echo"] = {}
    elif case == "governance-substitution":
        rows["governance/shared/safety.md"]["path"] = str(tmp_path / "other/shared/safety.md")
    elif case == "skill-substitution":
        rows["skill/deep-reference-protocol"]["path"] = str(tmp_path / "wrong-skill/SKILL.md")
    elif case == "duplicate-identity":
        rows["governance/shared/safety.md"].update(
            {key: rows["governance/AGENTS.md"][key] for key in ("device", "inode")},
        )
    elif case == "mixed-unc":
        rows["governance/AGENTS.md"]["path"] = "/\\fixture-host/share/AGENTS.md"
    elif case == "nt-namespace":
        rows["governance/AGENTS.md"]["path"] = r"\??\C:\fixture\AGENTS.md"
    elif case == "reserved-device":
        rows["governance/AGENTS.md"]["path"] = str(tmp_path / "NUL" / "AGENTS.md")
    monkeypatch.setattr("hermes_cli.config._platform_plugin_manifests", lambda **_: [])
    monkeypatch.setattr("hermes_cli.plugins_discovery._get_disabled_plugins", set)

    def unexpected_open(*args, **kwargs):
        pytest.fail("configuration checks must not open resource contents")

    with monkeypatch.context() as scope:
        scope.setattr(Path, "open", unexpected_open)
        diagnostics = config_check_diagnostics(config, lambda _: None)
    catalog_messages = [item for item in diagnostics if item.startswith("prerequisite_catalog:")]
    assert catalog_messages == ([f"prerequisite_catalog: {reason}"] if reason else [])


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
