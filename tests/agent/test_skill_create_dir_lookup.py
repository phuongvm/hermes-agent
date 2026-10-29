"""Skills under ``skills.create_dir`` resolve through every skill-root helper.

``get_skill_search_roots`` already ranks ``create_dir`` between the local profile dir and
``external_dirs``; the lookup helpers below used to consult only local + external roots, so an
absolute path (or a slash command) pointing into ``create_dir`` was rejected from a session whose
cwd sat outside the project.
"""

import os
from pathlib import Path

import pytest


def _skill_md(name: str) -> str:
    return (
        f"---\nname: {name}\n"
        f"description: Use when testing create dir lookup. One-line behavior.\n"
        f"---\n\n# {name}\n\nBody for {name}.\n"
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """HERMES_HOME with an empty local skills dir and a separate ``create_dir`` holding one
    categorised skill; cwd is moved outside any project."""
    home = tmp_path / ".hermes"
    (home / "skills").mkdir(parents=True)
    create_dir = tmp_path / "shared-skills"
    skill_dir = create_dir / "ops" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_md("demo-skill"), encoding="utf-8")
    outside = tmp_path / "outside-project"
    outside.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(home))
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "_hermes_home_cache", None, raising=False)
    (home / "config.yaml").write_text(
        f"skills:\n  create_dir: {create_dir.as_posix()}\n", encoding="utf-8")

    from agent import skill_utils as su
    from tools import skills_tool
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", home / "skills")
    monkeypatch.chdir(outside)
    su._raw_config_cache_clear()
    su._external_dirs_cache_clear()
    yield {"home": home, "create_dir": create_dir.resolve(), "skill_dir": skill_dir.resolve()}
    su._raw_config_cache_clear()
    su._external_dirs_cache_clear()


def test_normalize_absolute_path_under_create_dir(env):
    from agent.skill_utils import normalize_skill_lookup_name

    absolute = env["skill_dir"]
    assert normalize_skill_lookup_name(str(absolute)) == str(Path("ops") / "demo-skill")


def test_normalize_absolute_path_outside_every_root_is_unchanged(env, tmp_path):
    from agent.skill_utils import normalize_skill_lookup_name

    stray = tmp_path / "stray" / "skill"
    assert normalize_skill_lookup_name(str(stray)) == str(stray)


def test_category_from_path_under_create_dir(env):
    from tools.skills_tool import _get_category_from_path

    assert _get_category_from_path(env["skill_dir"] / "SKILL.md") == "ops"


def test_gateway_skill_iteration_includes_create_dir(env):
    from agent.skill_commands import scan_skill_commands
    from hermes_cli.commands_platforms import _iter_gateway_skills

    scan_skill_commands()
    found = {key: parts for key, _info, parts in _iter_gateway_skills("telegram")}
    assert found.get("/demo-skill") == ("ops", "demo-skill")


def test_slash_command_dispatch_from_cwd_outside_project(env):
    from agent.skill_commands import build_skill_invocation_message, scan_skill_commands

    assert not Path.cwd().resolve().is_relative_to(env["create_dir"])
    scan_skill_commands()
    message = build_skill_invocation_message("/demo-skill", "go")
    assert message and "Body for demo-skill" in message


def test_slash_command_dispatch_with_absolute_skill_identifier(env):
    """A command whose identifier is the absolute skill dir (as stored by older scans / cron)
    must still load."""
    from agent import skill_commands as sc

    loaded = sc._load_skill_payload(str(env["skill_dir"]))
    assert loaded is not None
    assert loaded[0]["name"] == "demo-skill"


def test_create_dir_path_is_externally_owned(env, tmp_path):
    from agent.skill_utils import is_external_skill_path

    assert is_external_skill_path(env["skill_dir"] / "SKILL.md") is True
    assert is_external_skill_path(env["home"] / "skills" / "mine" / "SKILL.md") is False
