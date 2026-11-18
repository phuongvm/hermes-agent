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
    monkeypatch.setattr("hermes_cli.config._platform_plugin_manifests", lambda **_: [])
    monkeypatch.setattr("hermes_cli.plugins_discovery._get_disabled_plugins", set)

    def unexpected_open(*args, **kwargs):
        pytest.fail("configuration checks must not open resource contents")

    with monkeypatch.context() as scope:
        scope.setattr(Path, "open", unexpected_open)
        diagnostics = config_check_diagnostics(config, lambda _: None)
    catalog_messages = [item for item in diagnostics if item.startswith("prerequisite_catalog:")]
    assert catalog_messages == ([f"prerequisite_catalog: {reason}"] if reason else [])
