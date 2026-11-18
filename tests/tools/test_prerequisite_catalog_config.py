"""Saved catalog validation reports bounded reasons, without opening resources."""
import os
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
    pytest.param("reserved-device", "unsupported-path", marks=pytest.mark.skipif(
        os.name != "nt", reason="Windows reserved path components")),
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
])
def test_catalog_rejects_incomplete_proposal_declaration(key, tmp_path):
    from agent.prerequisite_catalog import InvalidCatalog, freeze_proposal_catalog
    from tests.tools.proposal_catalog_fixture import CHANGE

    entry = proposal_entry(tmp_path / "planning")
    entry["resources"].pop(key, None)
    with pytest.raises(InvalidCatalog, match="^incomplete-resource-set$"):
        freeze_proposal_catalog(CHANGE, entry)
