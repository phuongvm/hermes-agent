## 1. Backend — Persistent Session Token Resolution

- [x] 1.1 Implement `_resolve_session_token()` durable file fallback in `hermes_cli/web_server.py`: read `$HERMES_HOME/.dashboard_session_token` when env var is unset; generate + write if file absent; set restrictive permissions (0600 Unix / ACL Windows).
- [x] 1.2 Add unit tests for token resolution: first-boot file creation, subsequent-boot file read, env-var precedence over file, corrupted/empty file regeneration.
- [x] 1.3 Verify backward compatibility: confirm existing `HERMES_DASHBOARD_SESSION_TOKEN` env var behavior is unchanged when set.

## 2. Backend — Startup Grace Period (503 Response)

- [x] 2.1 Add `_startup_ready` flag to the dashboard server initialization sequence; set `True` only after all routes and plugins are fully mounted.
- [x] 2.2 In `gated_auth_middleware` (or a pre-middleware guard), return HTTP 503 with `Retry-After: 3` when `_startup_ready is False` for any authenticated request, instead of processing auth and returning 401.
- [x] 2.3 Add unit tests: request during init → 503 with `Retry-After`; request after init → normal auth (200/401); grace period does not bypass auth for invalid tokens post-init.
- [x] 2.4 Add optional `dashboard.startup_grace_seconds` config.yaml setting (default 30s) as a hard ceiling on the grace period window.

## 3. Desktop Renderer — Connection-State-Gated Background Sync

- [x] 3.1 Identify and audit all background sync hooks that issue refresh calls: profile rail refresh, background sync, config refresh, model refresh. Catalog their trigger conditions (focus, visibility, WebSocket events).
- [x] 3.2 Add a connection-state gate to each hook: suppress refresh calls when gateway state is not `'open'`. Defer suppressed calls.
- [x] 3.3 Implement coalesced refresh-on-reconnect: when gateway transitions to `'open'`, fire exactly one refresh per data source using the latest cached state as baseline.
- [x] 3.4 Add tests: verify no IPC requests during `'connecting'` state; verify single coalesced refresh on reconnect; verify window focus during disconnect does not trigger refresh.

## 4. Desktop Renderer — Profile Store Error Absorption

- [x] 4.1 In the profile store (and equivalent session/config stores), wrap refresh error handlers to silently absorb network errors when gateway state is not `'open'`, preserving cached data.
- [x] 4.2 Ensure errors during stable `'open'` state still surface through normal error handling (toast/notification).
- [x] 4.3 Add tests: network error during reconnect → cached state preserved, no toast; network error during open → toast displayed.

## 5. Electron Main — Debounced Reauth Latch

- [x] 5.1 Implement two-tier 401 classifier in the backend health probe logic: track consecutive 401 count and timestamps per backend endpoint.
- [x] 5.2 Set `isReauthRequired` only when ≥2 consecutive 401s occur within 15 seconds AND the gateway was previously in `'open'` state.
- [x] 5.3 Reset the consecutive counter on any non-401 response (200, 503, timeout, network error).
- [x] 5.4 Add tests: single transient 401 during reconnect → no reauth; ≥2 consecutive 401s from stable → reauth triggered; mixed 401+200 → counter reset.

## 6. Electron Main — Single Global Reauth Modal

- [x] 6.1 Add a process-wide reauth latch (singleton) that prevents more than one re-auth modal from being displayed simultaneously.
- [x] 6.2 When a second pooled connection triggers `isReauthRequired` while a modal is active, queue it to wait for the active modal's outcome.
- [x] 6.3 On successful re-auth, resolve the auth state for all queued/waiting connections.
- [x] 6.4 Add tests: two simultaneous reauth triggers → one modal; successful reauth resolves both connections; failed reauth surfaces error once.

## 7. Integration Verification

- [x] 7.1 E2E test: restart backend with active Desktop client → Desktop reconnects without 401 pop-ups, no browser OAuth window, no IPC timeout errors.
- [x] 7.2 E2E test: restart backend with multiple pooled remote connections → at most one reauth modal if token genuinely changes.
- [x] 7.3 Regression test: genuine token revocation (delete token file + restart) → reauth flow triggers correctly within 15 seconds.
- [x] 7.4 Run full regression suite (`scripts/run_tests.sh`) and verify zero regressions.
