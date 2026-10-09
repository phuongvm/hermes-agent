# Exploration Report: Crew Remote Gateway Resolution

- **Date**: 2026-10-09
- **Topic**: Dynamic Gateway Resolution for Crew Dashboard (Hermes Desktop & Web Dashboard)
- **Target Submodules**: `oss/hermes-agent` and `oss/crew`

## 1. Problem Statement
When a user connects to a remote Hermes instance from Hermes Desktop or accesses Hermes Web Dashboard remotely:
1. `oss/crew/desktop/plugin.js` currently hardcodes the iframe `src` to `http://127.0.0.1:8799/`. On a client machine connected to a remote gateway, `127.0.0.1:8799` reaches the client machine's local loopback instead of the remote gateway, failing with `ERR_CONNECTION_REFUSED` or showing the client's local board.
2. In `oss/hermes-agent/hermes_cli/web_server.py`, the authentication middleware enforces token verification for all `/api/` endpoints. `_has_valid_query_token` only allows `/api/files/download`. An `<iframe>` cannot supply custom headers (`X-Hermes-Session` or `Authorization: Bearer`), so loading `/api/plugins/crew/board` over loopback or remote gateway returns `401 Unauthorized`.
3. In `oss/crew/dashboard/plugin_api.py`, child requests initiated from inside the iframe (e.g. `fetch("board.json")`) do not retain query parameters from the parent document URL unless a session cookie is established on the proxy path.

## 2. Architectural Seams & Seam Analysis
- **Electron IPC Bridge**: `window.hermesDesktop.getConnection()` provides `{ baseUrl, mode, remoteHost, token }`.
  - When `mode === 'remote'`, the desktop client resolves the board to `${baseUrl}/api/plugins/crew/board?token=${token}`.
  - When `mode === 'local'`, it connects to `http://127.0.0.1:8799/` with fallback to local proxy `${baseUrl}/api/plugins/crew/board`.
- **FastAPI Auth Middleware**: `web_server.py`'s `_has_valid_query_token()` should permit query tokens on `/api/plugins/` paths, and `_has_valid_session_token()` should recognize the `hermes_session` cookie.
- **Plugin Reverse Proxy**: `plugin_api.py` sets cookie `hermes_session=<token>` on `/api/plugins/crew/` when an authenticated token query parameter is received at `/board`, enabling all subsequent `fetch("board.json")` calls to succeed seamlessly.

## 3. Risk & Blast Radius
- Minimal and localized: Only affects `/api/plugins/` routes when a valid HMAC session token is supplied. Core chat, terminal, and session routing remain untouched.
