"""Regression tests for GET /v1/skills against real tools.skills_tool.find_all_skills."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from tools import skills_tool


def _skills_app() -> web.Application:
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={}))
    app = web.Application()
    app.router.add_get("/v1/skills", adapter._handle_skills)
    return app


@pytest.mark.asyncio
async def test_get_v1_skills_uses_real_find_all_skills_and_filters_disabled(
    tmp_path, monkeypatch
):
    """GET /v1/skills must invoke the real find_all_skills() without invalid kwargs
    (regression for stale include_editorial=True TypeError) and omit disabled skills."""
    hermes_home = tmp_path / "hermes_home"
    enabled_dir = hermes_home / "skills" / "ops" / "active-skill"
    disabled_dir = hermes_home / "skills" / "ops" / "muted-skill"
    enabled_dir.mkdir(parents=True)
    disabled_dir.mkdir(parents=True)
    (enabled_dir / "SKILL.md").write_text(
        "---\nname: active-skill\ndescription: Active ops skill\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (disabled_dir / "SKILL.md").write_text(
        "---\nname: muted-skill\ndescription: Disabled ops skill\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (hermes_home / "config.yaml").write_text(
        "skills:\n  disabled:\n    - muted-skill\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(skills_tool, "_SKILLS_CACHE", {})

    async with TestClient(TestServer(_skills_app())) as cli:
        resp = await cli.get("/v1/skills")
        assert resp.status == 200
        data = await resp.json()
        assert data["object"] == "list"
        assert data["data"] == [
            {
                "name": "active-skill",
                "description": "Active ops skill",
                "category": "ops",
            }
        ]
