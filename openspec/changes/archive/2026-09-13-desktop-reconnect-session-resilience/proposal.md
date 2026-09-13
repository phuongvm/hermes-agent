## Why

When the Hermes Gateway or Dashboard daemon restarts (configuration updates, plugin reloads, environment maintenance), Desktop users are bombarded with cascading "session timeout" pop-ups, spurious browser-based OAuth re-authentication windows, and 60-second IPC stalls. This happens because: (1) the session token is regenerated in-memory on every boot, instantly invalidating all connected clients; (2) Desktop background sync hooks fire simultaneously into a dead backend, creating a thundering-herd of promise rejections; and (3) a single transient 401 is misclassified as a hard auth revocation, triggering the full re-auth flow. This disrupts not only the restarting user but all Desktop sessions across the fleet.

## What Changes

- **Persistent session token resolution**: Fall back to a durable on-disk token file (`$HERMES_HOME/.dashboard_session_token`) when `HERMES_DASHBOARD_SESSION_TOKEN` is not set, so restarts preserve the existing token instead of minting a new one.
- **Startup grace period (503 over 401)**: During the backend initialization window (routes/plugins loading), auth middleware returns `503 Service Unavailable` with `Retry-After` instead of `401 session_expired`, allowing clients to back off and retry.
- **Connection-state-gated background sync**: Desktop renderer hooks (`useBackgroundSync`, `useProfileRailRefreshOnActive`) suppress refresh calls while `gatewayState !== 'open'`, preventing thundering-herd promise pile-ups.
- **Coalesced error absorption in profile store**: `store/profile.ts` silently absorbs network errors during reconnect transitions, maintaining cached profile state instead of surfacing uncaught promise rejections.
- **Hardened reauth latch with debounce**: Electron main process requires ≥2 consecutive 401 failures within a 15-second window before escalating to `isReauthRequired` and launching the browser loopback. Only one global re-auth modal is allowed across all pooled remote connections.

## Capabilities

### New Capabilities
- `desktop-reconnect-resilience`: Covers the Desktop client's ability to gracefully survive gateway/dashboard restarts without pop-up storms, spurious re-auth, or IPC stalls. Encompasses connection-state gating, error coalescing, and reauth latch debouncing.

### Modified Capabilities
- `dashboard-auth`: The auth middleware gains a startup grace period (503 response during initialization) and the session token resolution falls back to persistent on-disk storage. These are requirement-level changes to existing auth behavior.

## Impact

- **Backend** (`hermes_cli/web_server.py`, `hermes_cli/dashboard_auth/`): Token resolution logic and middleware startup behavior change. No breaking API changes — 503 is a standard retryable status code.
- **Desktop Renderer** (`apps/desktop/src/`): Background sync hooks, profile store error handling. Existing behavior preserved when gateway is healthy; changes activate only during reconnect windows.
- **Electron Main** (`apps/desktop/electron/`): Backend health probe classification, reauth latch logic. The current aggressive single-401 escalation is replaced with a debounced multi-probe gate.
- **Multi-connection pool**: All pooled remote backends benefit from the single global reauth modal constraint.
- **No breaking changes**. All modifications are backward-compatible — pinned `HERMES_DASHBOARD_SESSION_TOKEN` env var continues to take precedence; healthy-state Desktop behavior is unchanged.
