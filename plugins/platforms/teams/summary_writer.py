"""Pipeline-facing Teams outbound delivery (meeting-summary writer).

Lives inside the Teams platform plugin so the meeting pipeline reuses one Teams
"""

from __future__ import annotations

import html
import hashlib
import json
import os
from typing import Any, Optional
from urllib.parse import quote

from gateway.config import PlatformConfig
from gateway.platforms._shared import get_scoped_secret as _get_scoped_secret

import httpx


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


_LIST_SECTIONS = (("Key decisions", "key_decisions"), ("Action items", "action_items"), ("Risks", "risks"))
# Env fallbacks for delivery config keys, applied only where nothing else set the key (access_token is a scoped secret).
_ENV_KEYS = {"delivery_mode": "TEAMS_DELIVERY_MODE", "incoming_webhook_url": "TEAMS_INCOMING_WEBHOOK_URL",
             "access_token": "TEAMS_GRAPH_ACCESS_TOKEN", "team_id": "TEAMS_TEAM_ID", "channel_id": "TEAMS_CHANNEL_ID", "chat_id": "TEAMS_CHAT_ID"}


class _StaticAccessTokenProvider:
    """Minimal token-provider shim so outbound Graph delivery can reuse the shared client."""

    def __init__(self, access_token: str):
        self._access_token = str(access_token or "").strip()

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not self._access_token:
            raise ValueError("TEAMS_GRAPH_ACCESS_TOKEN is required for graph delivery mode.")
        return self._access_token

    def clear_cache(self) -> None:
        return None


def _compute_summary_hash(payload: Any) -> str:
    """Compute a SHA-256 hash of the summary content for dedup comparison.

    Hashes the structural summary fields (title, summary, action_items,
    key_decisions, risks) so that recurring meetings with new transcripts
    produce different hashes and trigger re-delivery.
    """
    if hasattr(payload, "to_dict"):
        d = payload.to_dict()
    elif isinstance(payload, dict):
        d = payload
    else:
        d = {}

    content = json.dumps(
        {
            "title": d.get("title"),
            "summary": d.get("summary"),
            "action_items": sorted(d.get("action_items") or []),
            "key_decisions": sorted(d.get("key_decisions") or []),
            "risks": sorted(d.get("risks") or []),
            "start_time": d.get("start_time"),
            "end_time": d.get("end_time"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(content.encode()).hexdigest()

class _DelegatedOAuthTokenProvider:
    """OAuth2 token provider that uses authorization code + refresh token flow.

    Loads tokens from ~/.hermes/teams_oauth_tokens.json and auto-refreshes
    when the access token is expired or about to expire.
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        token_path: str | None = None,
        transport: Any | None = None,
    ):
        import os as _os
        self._client_id = client_id or _os.getenv("MSGRAPH_CLIENT_ID", "")
        self._client_secret = client_secret or _os.getenv("MSGRAPH_CLIENT_SECRET", "")
        self._tenant_id = tenant_id or _os.getenv("MSGRAPH_TENANT_ID", "")
        self._token_path = token_path or _os.path.expanduser("~/.hermes/teams_oauth_tokens.json")
        self._transport = transport
        self._tokens: dict[str, Any] = {}

    def _load_tokens(self) -> dict[str, Any]:
        import os as _os
        if _os.path.exists(self._token_path):
            with open(self._token_path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_tokens(self, tokens: dict[str, Any]) -> None:
        import os as _os
        _os.makedirs(_os.path.dirname(self._token_path) or ".", exist_ok=True)
        with open(self._token_path, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            tokens = self._load_tokens()
            if tokens and tokens.get("access_token"):
                import time
                expires_at = tokens.get("expires_at") or 0
                # Refresh if less than 60 seconds remaining
                if time.time() < float(expires_at) - 60:
                    return tokens["access_token"]

        tokens = self._load_tokens()
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise ValueError(
                "No delegated OAuth tokens found. Run 'hermes teams-pipeline auth login' first."
            )

        import time
        import httpx
        token_url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
        }

        # Use self._transport if available (for testing)
        client_kwargs: dict[str, Any] = {"timeout": 30.0}
        if self._transport:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(token_url, data=data)
            if resp.status_code != 200:
                raise ValueError(
                    f"Token refresh failed ({resp.status_code}): {resp.text[:300]}"
                )
            new_tokens = resp.json()
            # Set expiry timestamp
            new_tokens["expires_at"] = time.time() + new_tokens.get("expires_in", 3600)
            self._save_tokens(new_tokens)
            return new_tokens["access_token"]

    def clear_cache(self) -> None:
        pass

class TeamsSummaryWriter:
    """Pipeline-facing Teams outbound delivery surface.

    This stays inside the existing Teams platform plugin so the meeting-pipeline
    PR can reuse one Teams integration surface instead of introducing a second
    adapter elsewhere in the gateway core.
    """

    def __init__(
        self,
        platform_config: PlatformConfig | None = None,
        *,
        graph_client: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._platform_config = platform_config
        self._graph_client = graph_client
        self._transport = transport

    async def write_summary(
        self,
        payload: Any,
        config: dict[str, Any] | None,
        existing_record: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        merged = self._resolve_delivery_config(config)

        # Dedup: skip delivery only if content hasn't changed.
        # For recurring meetings with new transcripts, the summary content
        # will differ — so we detect the hash mismatch and re-deliver.
        if existing_record and not _parse_bool(merged.get("force_resend"), default=False):
            new_hash = _compute_summary_hash(payload)
            stored_hash = existing_record.get("content_hash")
            if stored_hash and stored_hash == new_hash:
                return dict(existing_record)
            # Hash mismatch or no stored hash → deliver (new content)

        mode = str(merged.get("delivery_mode") or merged.get("mode") or "").strip().lower()
        if not mode:
            if merged.get("incoming_webhook_url"):
                mode = "incoming_webhook"
            elif merged.get("chat_id") or (
                merged.get("team_id") and merged.get("channel_id")
            ):
                mode = "graph"

        # Extract meeting chat ID from payload metadata (thread_id)
        meeting_chat_id = None
        try:
            meeting_ref = getattr(payload, "meeting_ref", None)
            if meeting_ref:
                metadata = getattr(meeting_ref, "metadata", None) or {}
                meeting_chat_id = (
                    metadata.get("thread_id")
                    or metadata.get("recap_data", {}).get("threadId")
                )
                # Fallback: extract from join_web_url
                if not meeting_chat_id:
                    join_url = getattr(meeting_ref, "join_web_url", "") or ""
                    if join_url:
                        import urllib.parse as _up
                        decoded = _up.unquote(join_url)
                        import re as _re
                        m = _re.search(r"(19:meeting_[A-Za-z0-9_\-]+@thread\.v2)", decoded)
                        if m:
                            meeting_chat_id = m.group(1)
        except Exception:
            pass

        if mode == "incoming_webhook":
            result = await self._write_summary_via_incoming_webhook(payload, merged)
        elif mode == "graph":
            result = await self._write_summary_via_graph(payload, merged, meeting_chat_id=meeting_chat_id)
        else:
            raise ValueError(
                "Teams delivery_mode must be 'incoming_webhook' or 'graph'."
            )

        # Store content hash for future dedup comparison
        result["content_hash"] = _compute_summary_hash(payload)
        return result

    def _resolve_delivery_config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        platform_cfg = self._platform_config
        if platform_cfg is not None:
            merged.update(dict(platform_cfg.extra or {}))
            if platform_cfg.token and "access_token" not in merged:
                merged["access_token"] = platform_cfg.token
            if platform_cfg.home_channel:
                merged.setdefault("channel_id", platform_cfg.home_channel.chat_id)
        merged.update(dict(config or {}))

        env_defaults = {
            "delivery_mode": os.getenv("TEAMS_DELIVERY_MODE", ""),
            "incoming_webhook_url": os.getenv("TEAMS_INCOMING_WEBHOOK_URL", ""),
            "access_token": _get_scoped_secret("TEAMS_GRAPH_ACCESS_TOKEN", ""),
            "team_id": os.getenv("TEAMS_TEAM_ID", ""),
            "channel_id": os.getenv("TEAMS_CHANNEL_ID", ""),
            "chat_id": os.getenv("TEAMS_CHAT_ID", ""),
        }
        for key, value in env_defaults.items():
            if value and not merged.get(key):
                merged[key] = value
        return merged

    async def _write_summary_via_incoming_webhook(
        self,
        payload: Any,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        # Lazy import — see module-level note. The teams plugin loads on
        # every CLI invocation as a side effect of plugin discovery, but
        # 99% of those processes never reach this method.
        import httpx
        webhook_url = str(config.get("incoming_webhook_url") or "").strip()
        if not webhook_url:
            raise ValueError("TEAMS_INCOMING_WEBHOOK_URL is required for incoming_webhook mode.")
        body = {"text": self._render_summary_markdown(payload)}
        async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as client:
            response = await client.post(webhook_url, json=body)
            response.raise_for_status()
        return {
            "delivery_mode": "incoming_webhook",
            "webhook_url": webhook_url,
            "status_code": response.status_code,
            "delivered": True,
        }

    async def _write_summary_via_graph(
        self,
        payload: Any,
        config: dict[str, Any],
        *,
        meeting_chat_id: str | None = None,
    ) -> dict[str, Any]:
        graph_client = self._build_graph_client(config)
        # Priority: meeting chat ID (dynamic) > config chat_id > team/channel
        chat_id = str(meeting_chat_id or "").strip()
        if not chat_id:
            chat_id = str(config.get("chat_id") or "").strip()
        if chat_id:
            path = f"/chats/{quote(chat_id, safe='')}/messages"
            response = await graph_client.post_json(
                path,
                json_body={"body": {"contentType": "html", "content": self._render_summary_html(payload)}},
            )
            return {
                "delivery_mode": "graph",
                "target_type": "chat",
                "chat_id": chat_id,
                "message_id": (response or {}).get("id"),
                "web_url": (response or {}).get("webUrl"),
            }

        team_id = str(config.get("team_id") or "").strip()
        channel_id = str(config.get("channel_id") or "").strip()
        if not team_id or not channel_id:
            raise ValueError(
                "Graph delivery mode requires chat_id, or both team_id and channel_id."
            )
        path = (
            f"/teams/{quote(team_id, safe='')}/channels/"
            f"{quote(channel_id, safe='')}/messages"
        )
        response = await graph_client.post_json(
            path,
            json_body={"body": {"contentType": "html", "content": self._render_summary_html(payload)}},
        )
        return {
            "delivery_mode": "graph",
            "target_type": "channel",
            "team_id": team_id,
            "channel_id": channel_id,
            "message_id": (response or {}).get("id"),
            "web_url": (response or {}).get("webUrl"),
        }

    def _build_graph_client(self, config: dict[str, Any]) -> Any:
        if self._graph_client is not None:
            return self._graph_client

        from tools.microsoft_graph_auth import MicrosoftGraphTokenProvider
        from tools.microsoft_graph_client import MicrosoftGraphClient

        # PRIORITY 1: Delegated OAuth token (can post to user chats)
        # Try first — if tokens exist, use delegated auth for delivery
        try:
            delegated = _DelegatedOAuthTokenProvider(transport=self._transport)
            # Test that we can get a token (non-destructive check)
            import os as _os
            token_path = _os.path.expanduser("~/.hermes/teams_oauth_tokens.json")
            if _os.path.exists(token_path):
                return MicrosoftGraphClient(delegated, transport=self._transport)  # type: ignore[arg-type]
        except (ValueError, ImportError):
            pass  # Fall through to app-only or static token

        # PRIORITY 2: Static access token from config
        access_token = str(config.get("access_token") or "").strip()
        if access_token:
            return MicrosoftGraphClient(
                _StaticAccessTokenProvider(access_token),
                transport=self._transport,
            )

        # PRIORITY 3: App-only token (can read callRecords but cannot post to chats)
        return MicrosoftGraphClient(
            MicrosoftGraphTokenProvider.from_env(),
            transport=self._transport,
        )

    def _render_summary_markdown(self, payload: Any) -> str:
        lines = [
            f"**{self._title(payload)}**",
            "",
            f"Summary: {self._text(getattr(payload, 'summary', None), 'No summary available.')}",
            "",
            "Key decisions:",
            *self._bullet_lines(getattr(payload, "key_decisions", None)),
            "",
            "Action items:",
            *self._bullet_lines(getattr(payload, "action_items", None)),
            "",
            "Risks:",
            *self._bullet_lines(getattr(payload, "risks", None)),
        ]
        return "\n".join(lines)

    def _render_summary_html(self, payload: Any) -> str:
        # Use pre-rendered template-driven HTML when available (pipeline v1.0+)
        rendered = getattr(payload, "rendered_html", None)
        if rendered and isinstance(rendered, str) and rendered.strip():
            return rendered

        # Fallback: hardcoded HTML renderer (original behavior)
        sections = [
            ("Summary", [self._text(getattr(payload, "summary", None), "No summary available.")]),
            ("Key decisions", list(getattr(payload, "key_decisions", None) or [])),
            ("Action items", list(getattr(payload, "action_items", None) or [])),
            ("Risks", list(getattr(payload, "risks", None) or [])),
        ]
        blocks = [f"<h2>{html.escape(self._title(payload))}</h2>"]
        for heading, items in sections:
            blocks.append(f"<h3>{html.escape(heading)}</h3>")
            if len(items) == 1 and heading == "Summary":
                blocks.append(f"<p>{html.escape(str(items[0]))}</p>")
                continue
            if items:
                rendered = "".join(f"<li>{html.escape(str(item))}</li>" for item in items if str(item).strip())
                blocks.append(rendered and f"<ul>{rendered}</ul>" or "<p>None</p>")
            else:
                blocks.append("<p>None</p>")
        return "".join(blocks)

    @staticmethod
    def _title(payload: Any) -> str:
        title = getattr(payload, "title", None)
        if title:
            return str(title)
        meeting_ref = getattr(payload, "meeting_ref", None)
        meeting_id = getattr(meeting_ref, "meeting_id", None) if meeting_ref else None
        return f"Meeting {meeting_id or 'summary'}"

    @staticmethod
    def _text(value: Any, default: str) -> str:
        text = str(value or "").strip()
        return text or default

    @classmethod
    def _bullet_lines(cls, values: Any) -> list[str]:
        items = [str(item).strip() for item in (values or []) if str(item).strip()]
        return [f"- {item}" for item in items] or ["- None"]
