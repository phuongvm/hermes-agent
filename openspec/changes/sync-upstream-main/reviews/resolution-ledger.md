# Upstream Reconciliation Resolution Ledger

## 1. Provenance & Preflight
- **Local Base**: `c57316beafaba4e27b9797e137430619f98a899d` (`origin/main`)
- **Upstream Target**: `24fd22b94df040d843eb280ff197a4bcd99a6fc3` (`upstream/main`, ahead by 2,704 commits)
- **Initial Merge Commit**: `5686404bc1839510cf1e7fb53db6f268c19a3e27` (`sync/upstream-main`)
- **Remediation Worktree**: `O:/workspaces/oss/hermes-agent/.worktrees/sync-upstream-remediation-r1` (`wt/t_33997cbc`)

---

## 2. Invariant Preservation Audit Matrix

| Invariant | Description | Preservation Status | Evidence & Test Suite |
| :--- | :--- | :--- | :--- |
| **P0 Auth (Invariant 1)** | Process-Home Session Binding (`bfc288316d`) & token persistence | **PRESERVED (100%)** | `tests/hermes_cli/test_dashboard_auth_native_flow.py` (17/17 pass), `test_oidc_application_sessions.py` (pass) |
| **Native 401 (Invariant 2)** | Single-Replay & `rejectedBearer` force-refresh (`25668efb1e`, `37c373e723`) | **PRESERVED (100%)** | `apps/desktop/electron/native-auth-decisions.test.ts` (31/31 vitest pass) |
| **Reconnect Resilience (Invariant 3)** | 3-tier desktop reconnect & session lifetime (`ce2977e007`) | **PRESERVED (100%)** | `apps/desktop/src/store/profile.test.ts` (24/24 vitest pass), `apps/desktop/src/hermes.test.ts` (38/38 vitest pass) |
| **Buzz WAN (Invariant 4)** | WebSocket keepalive & non-editable delivery (`3b0aa33e21`, `28a121de2d`) | **PRESERVED (100%)** | `tests/gateway/test_buzz_websocket.py` (24/24 pytest pass) |
| **JWKS & Chooser (Invariant 5)** | Multi-provider JWKS kid classification & single non-password auto-select | **PRESERVED (100%)** | `hermes_cli/dashboard_auth/routes.py` auto-selects lone OAuth, chooser for multiple |

---

## 3. Detailed Conflict File Resolution & Strategy

### 3.1. `apps/desktop/electron/main.ts` (19 Conflict Blocks)
- **Nature of Conflict**: Upstream refactored token management into `native-access-token.ts` with `createNativeAccessTokenController`, while local codebase added TwoTier401Classifier, `rejectedBearer` tracking, and singleton reauth modal latch.
- **Resolution Strategy**: Integrated upstream's modular controller interface while embedding local single-replay execution, force-refresh triggers, and generation tracking inside the IPC token request pipeline.
- **Verification**: `npx tsc --noEmit` clean, `native-auth-decisions.test.ts` (31/31 passed).

### 3.2. `apps/desktop/electron/native-auth-decisions.ts` & `native-auth-decisions.test.ts`
- **Nature of Conflict**: Divergence in helper functions for token evaluation.
- **Resolution Strategy**: Preserved local single-replay logic and unexpired 401 retry guard. Merged all upstream test assertions without dropping local invariant test cases.
- **Verification**: 31/31 vitest tests passed.

### 3.3. `apps/desktop/src/store/profile.ts`
- **Nature of Conflict**: Upstream changes to profile switching vs local terminal signed-out suspension and reconnect error absorption.
- **Resolution Strategy**: Kept local disconnected cache retention, immediate terminal-auth suspension, and coalesced refresh on reconnect.
- **Verification**: 24/24 vitest tests passed (`src/store/profile.test.ts`).

### 3.4. `apps/desktop/src/app/session/hooks/use-model-controls.ts`
- **Nature of Conflict**: Upstream added fine-grained model controls; local added gateway connection guard.
- **Resolution Strategy**: Retained all upstream model control features while wrapping background profile refresh calls in gateway state checks.
- **Verification**: Vitest suite passed.

### 3.5. `hermes_cli/dashboard_auth/routes.py` & `login_page.py`
- **Nature of Conflict**: Upstream off-event-loop refresh singleflight vs local process-home provider binding and D6 chooser auto-selection.
- **Resolution Strategy**: Maintained process-home binding in `_select_native_provider()`; auto-selects lone non-password provider (SSO-with-password setup) avoiding unexpected 404/chooser; renders chooser only when 2+ interactive OAuth providers exist.
- **Verification**: `test_dashboard_auth_native_flow.py` (17/17 passed).

### 3.6. `hermes_cli/dashboard_auth/refresh_singleflight.py`
- **Nature of Conflict**: Upstream monotonic 30s session caching returning stale access token after expiry.
- **Resolution Strategy**: Enhanced `_refresh_provider` cache check with `expires_at` and `provider.verify_session()` validity checks before returning cached session.
- **Verification**: `test_native_http_refresh_ws_ticket_and_logout_after_id_token_expiry` passed.

### 3.7. `hermes_cli/mcp_startup.py`
- **Nature of Conflict**: Profile discovery started set tracking vs disabled configuration check.
- **Resolution Strategy**: Early return if `not _has_configured_mcp_servers()` before adding profile key to `_mcp_discovery_started`.
- **Verification**: `test_background_discovery_skips_when_all_servers_disabled` passed.

### 3.8. `hermes_cli/kanban_db.py`
- **Nature of Conflict**: Upstream PID/start-time fingerprints and stale claim handling vs local parent dependency gating and structured completion handoffs.
- **Resolution Strategy**: Fully unified both branches: kept upstream fingerprinting and stale claim recovery while preserving local link/unlink and handoff mechanics.
- **Verification**: SQLite kanban CLI and tests pass.

### 3.9. `hermes_cli/web_routers/files.py`
- **Nature of Conflict**: Centralized sensitive path traversal checks vs upstream preview enhancements.
- **Resolution Strategy**: Preserved strict file path validation and sensitive blocklist while integrating upstream preview capabilities.
- **Verification**: Pytest files router suite passed.

### 3.10. `gateway/platforms/base.py`
- **Nature of Conflict**: Upstream proxy/media additions vs local streaming and editing capability guards.
- **Resolution Strategy**: Merged upstream media handling while preserving `SUPPORTS_MESSAGE_EDITING` and Buzz adapter non-editable single-delivery invariants.
- **Verification**: `tests/gateway/test_buzz_websocket.py` (24/24 passed).

### 3.11. `apps/desktop/package.json` & `package-lock.json`
- **Nature of Conflict**: Electron version mismatch (`^43.3.0` vs builder `40.10.2`).
- **Resolution Strategy**: Aligned `electron` dependency to exact `"40.10.2"` matching `build.electronVersion: "40.10.2"`; restored exact lockfile entries from upstream/main.
- **Verification**: `electron/desktop-electron-pin.test.ts` (3/3 passed).

### 3.12. `apps/desktop/src/hermes.test.ts`
- **Nature of Conflict**: Test assumed active profile refresh called API unconditionally during desktop startup, but production `profile.ts` guards calls behind `isGatewayOpen()`.
- **Resolution Strategy**: Explicitly initialized `$gatewayState.set('open')` in test and reset to `'idle'` in `afterEach`.
- **Verification**: `src/hermes.test.ts` (38/38 passed).

---

## 4. Summary of Test Proof & Verification

| Suite / Target | Command | Result |
| :--- | :--- | :--- |
| **Electron Pin Suite** | `npx vitest run --project electron electron/desktop-electron-pin.test.ts` | **3 passed (100%)** |
| **Desktop TypeScript** | `npx tsc --noEmit` | **Exit 0 (0 errors)** |
| **Desktop Auth Decisions** | `npx vitest run electron/native-auth-decisions.test.ts` | **31 passed (100%)** |
| **Desktop Profile Store** | `npx vitest run src/store/profile.test.ts` | **24 passed (100%)** |
| **Desktop REST & Startup** | `npx vitest run --project ui src/hermes.test.ts` | **38 passed (100%)** |
| **MCP Startup Discovery** | `pytest tests/hermes_cli/test_mcp_startup.py::test_background_discovery_skips_when_all_servers_disabled` | **1 passed (100%)** |
| **Native Auth Flow** | `pytest tests/hermes_cli/test_dashboard_auth_native_flow.py` | **17 passed (100%)** |
| **OIDC Session Expiry** | `pytest tests/plugins/dashboard_auth/test_oidc_application_sessions.py::test_native_http_refresh_ws_ticket_and_logout_after_id_token_expiry` | **1 passed (100%)** |
| **Buzz WAN WebSocket** | `pytest tests/gateway/test_buzz_websocket.py` | **24 passed (100%)** |
| **OpenSpec Strict Validation** | `openspec validate sync-upstream-main --strict` | **Change is valid (100%)** |
