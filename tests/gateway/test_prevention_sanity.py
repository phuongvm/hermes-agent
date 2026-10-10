"""Prevention Sanity Test Suite: Invariant Gate for aiohttp, Gateway Adapters, and DeskRPG.

Verifies that:
1. aiohttp.web is importable and web_server.py is not corrupted by hermes_cli/web_server.py.
2. WebhookAdapter and APIServerAdapter requirements are satisfied.
3. deskrpg_plugin.common and review_hooks import cleanly.
"""

from pathlib import Path
import aiohttp
from aiohttp import web


def test_aiohttp_web_server_file_integrity():
    server_file = Path(aiohttp.__file__).parent / "web_server.py"
    assert server_file.exists(), "aiohttp/web_server.py must exist"
    assert server_file.stat().st_size < 10000, (
        f"aiohttp/web_server.py is corrupted ({server_file.stat().st_size} bytes; expected ~3.2KB)"
    )


def test_aiohttp_web_server_class_available():
    from aiohttp.web_server import Server
    assert Server is not None


def test_gateway_adapter_requirements():
    from gateway.platforms.webhook import check_webhook_requirements
    from gateway.platforms.api_server import check_api_server_requirements

    assert check_webhook_requirements(), "Webhook adapter requirement check failed"
    assert check_api_server_requirements(), "API Server adapter requirement check failed"


def test_deskrpg_plugin_review_hooks_importable():
    import sys
    sys.path.insert(0, r"O:\workspaces\_config\agent4070\hermes\plugins\deskrpg")
    from deskrpg_plugin import review_hooks
    assert review_hooks is not None
