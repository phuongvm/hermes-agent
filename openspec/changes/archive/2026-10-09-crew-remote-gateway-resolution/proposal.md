# Proposal: Crew Remote Gateway Resolution

## 1. Executive Summary
Enable both Hermes Desktop and Hermes Web Dashboard to seamlessly display the Crew Dashboard when connecting to local instances or remote gateways.

## 2. Motivation
Users accessing Hermes from remote environments (e.g. desktop app connecting to remote NUC/cluster over network/Tailscale, or browser hitting remote dashboard) currently cannot view the Crew dashboard because:
1. Hermes Desktop hardcodes `http://127.0.0.1:8799/`, reaching client loopback instead of the gateway.
2. The FastAPI `auth_middleware` rejects iframe requests to `/api/plugins/crew/board` with 401 because iframes cannot set custom `X-Hermes-Session` headers and `?token=` was restricted to file downloads.
3. Subpath queries inside the iframe (`fetch("board.json")`) lack session tokens without proxy-scoped session cookies.

## 3. Scope of Changes
- `oss/crew/desktop/plugin.js`: Dynamically resolve board URL via `window.hermesDesktop.getConnection()`. If remote, use `${baseUrl}/api/plugins/crew/board?token=${token}`. If local, use `http://127.0.0.1:8799/`.
- `oss/hermes-agent/hermes_cli/web_server.py`: Expand `_has_valid_query_token` to accept tokens on `/api/plugins/` paths, and accept `hermes_session` cookie in `_has_valid_session_token`.
- `oss/crew/dashboard/plugin_api.py`: Set path-scoped `hermes_session` cookie on `/board` response when token query param is provided, preserving authentication for asynchronous polling (`board.json`).

## 4. Verification Criteria
- Unit tests verify dynamic resolution logic in desktop plugin.
- Python tests verify token query parameter acceptance and cookie validation on `/api/plugins/` endpoints.
- `openspec validate crew-remote-gateway-resolution --strict` exits with 0.
