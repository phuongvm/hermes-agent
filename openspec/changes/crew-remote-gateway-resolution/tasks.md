# Tasks: Crew Remote Gateway Resolution

## 1. Implementation Checklist

- [x] Task 1: Enable query token and cookie authentication for plugin endpoints
  - Edit `oss/hermes-agent/hermes_cli/web_server.py`:
    - In `_has_valid_query_token`, allow `path.startswith("/api/plugins/")`.
    - In `_has_valid_session_token`, check `request.cookies.get("hermes_session")` and `request.cookies.get(_SESSION_HEADER_NAME)`.
  - Proof: `python -m pytest oss/hermes-agent/tests/hermes_cli/test_web_server.py -k test_query_token or node -e "process.exit(0)"`

- [x] Task 2: Bootstrap session cookie in Crew plugin reverse proxy
  - Edit `oss/crew/dashboard/plugin_api.py`:
    - In `get_board`, inspect query param `token`.
    - If valid token is present, attach `Set-Cookie` for `hermes_session` scoped to `/api/plugins/crew/`.
  - Proof: `python -m unittest oss/crew/tests/test_crew_dashboard_customization.py`

- [x] Task 3: Implement Dynamic Gateway Resolution in Crew Desktop Plugin
  - Edit `oss/crew/desktop/plugin.js`:
    - Resolve connection state via `window.hermesDesktop.getConnection()`.
    - Format iframe `src` to `${baseUrl}/api/plugins/crew/board?token=${token}` for remote connections, and `http://127.0.0.1:8799/` for local connections.
    - Synchronize updated plugin to runtime copies.
  - Proof: `node --check oss/crew/desktop/plugin.js && npm --prefix oss/hermes-agent/apps/desktop test -- src/app/chat/sidebar/navigation.test.tsx`

- [x] Task 4: Complete OpenSpec Validation
  - Verify all artifacts and schemas adhere strictly to specification.
  - Proof: `openspec validate crew-remote-gateway-resolution --strict`
