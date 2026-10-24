"""HTTP routes for memory-provider OAuth connect, mounted by ``web_server``."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/memory/providers")


def _resolve_flow(provider: str):
    """Return a provider's ``oauth_flow`` module (bundled or user-dir copy), or raise 404."""
    if not provider.isidentifier():
        raise HTTPException(status_code=404, detail=f"unknown memory provider {provider!r}")
    from plugins.memory import import_provider_module

    try:
        return import_provider_module(provider, "oauth_flow")
    except ImportError:
        raise HTTPException(status_code=404, detail=f"{provider} does not support OAuth connect")


@contextmanager
def _scope_to_profile(profile: Optional[str]):
    """Scope config AND secret resolution to ``profile`` so the flow's eager path resolve targets that
    profile's honcho.json and its ``get_secret`` reads (``HERMES_HONCHO_HOST``) resolve from that
    profile. None/""/"current" bind the dashboard's own (effective) profile's secret scope: under
    multiplex an unscoped read raises ``UnscopedSecretError`` and the route answered HTTP 500."""
    from hermes_cli.web_server_profiles import _config_profile_scope

    with _config_profile_scope(profile):
        yield


@router.post("/{provider}/oauth/start")
async def start_memory_oauth(provider: str, profile: Optional[str] = None):
    """Begin a provider's zero-CLI OAuth flow (browser + loopback listener); returns immediately, poll status."""
    try:
        # The flow resolves its config path eagerly inside this scope; its worker thread outlives it.
        with _scope_to_profile(profile):
            flow = _resolve_flow(provider)
            return flow.start_loopback_flow_background()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start {provider} OAuth: {exc}")


@router.get("/{provider}/oauth/status")
async def memory_oauth_status(provider: str, profile: Optional[str] = None):
    """Poll a provider's OAuth flow: idle | pending | connected | error."""
    try:
        with _scope_to_profile(profile):
            flow = _resolve_flow(provider)
            return flow.get_flow_status()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read {provider} OAuth status: {exc}")
