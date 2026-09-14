# Design: Stabilize Dashboard Auth Runtime Ownership and Terminal Signed-Out Behavior

## Context
During extended remote Desktop usage, users experience recurrent forced re-identification, auth drops, and cascading 401 retry storms. Investigation of the root cause in task `t_3cb6a9dd` established:

1. **Backend Host-Auth Replacement Defect**:
   - `SelfHostedOIDCProvider` captures `get_hermes_home() / "dashboard-auth-sessions.db"` at construction time (`plugins/dashboard_auth/self_hosted/__init__.py:89-96`).
   - `PluginContext.register_dashboard_auth_provider()` upserts this provider directly into the process-global auth registry (`hermes_cli/plugins.py:739-765`; `hermes_cli/dashboard_auth/registry.py:96-108`).
   - When per-request routed profile execution or profile-scoped plugin rediscovery occurs, `get_hermes_home()` resolves to that profile's home directory (e.g., `profiles/coder/`). A rediscovery constructs a new `SelfHostedOIDCProvider` pointing to `profiles/coder/dashboard-auth-sessions.db` and overwrites the process-global provider.
   - Consequently, when the host web server verifies or refreshes native application session tokens (`hermes-oidc-rt.*`), it queries the wrong profile database. Because native refresh of `hermes-oidc-rt.*` tokens is purely SQLite-based (`self_hosted/__init__.py:120-125`; `sessions.py:120-133`), the lookup fails with `all_providers_rejected_rt` (HTTP 401).
   - Real-world evidence confirms this pattern in `dashboard-auth.log:11710-11721`: a session created at 06:51 in the root store was dropped at 07:00 after profile activity, a subsequent login at 07:01 created a session in coder store, and a refresh 5 seconds later failed. Cross-profile DB inspection revealed sessions fragmented across root, coder, designer, leader, and reviewer stores.

2. **Desktop Background Request Storm Defect**:
   - When the Desktop client receives an authoritative 401 rejection from `/auth/native/refresh`, `createNativeTokenRefresher` clears stored tokens locally (`apps/desktop/electron/native-token-refresh.ts:57-61`).
   - However, the Desktop does not atomically transition the normalized base URL into an explicit `reauth-required` / `signed-out` state.
   - Background renderer hooks and polling loops continue firing unauthenticated HTTP/IPC calls (`refreshProfiles` in `store/profile.ts:109-155`, `useProfileRailRefreshOnActive.ts:31-64`, and `use-project-tree.ts:143-146, 492-525`).
   - Because the gateway connection is technically `'open'`, these requests reach the server with missing cookies (`reason=no_cookie`) or missing bearer tokens, triggering a continuous 401 cascade (`desktop.log:24305-24368` recorded 29 profile refresh failures and multiple uncaught promise rejections).
   - In `boot-failure-overlay.tsx:175-181`, the recovery flow calls `oauthLogoutConnectionConfig` before re-login (`dashboard-auth.log:11734`), proving that explicit logout is part of intentional recovery and must remain revoking and destructive, while unauthenticated background traffic must be halted.

## Goals / Non-Goals

**Goals:**
- Bind the host dashboard auth provider and its SQLite session store immutably to the host process home for the entire lifetime of the dashboard server process.
- Prevent request-scoped or routed profile rediscovery from replacing or mutating the active host-owned auth provider in the process-global registry.
- Preserve process-level multi-profile isolation: explicitly launched named-profile dashboards remain bound to their respective profile homes.
- Transition Desktop into an authoritative `signed-out` / `reauth-required` state per normalized base URL upon terminal refresh rejection.
- Suppress Desktop background network calls, retry timers, notifications, and uncaught promise rejections while in the terminal signed-out state.
- Protect valid newer credentials from being overwritten or cleared by stale in-flight rejections using compare-and-set / generation awareness.
- Reset the terminal state upon confirmed sign-in, resuming and coalescing deferred background operations exactly once.
- Preserve explicit user logout semantics: maintain server-side session revocation and local token destruction.

**Non-Goals:**
- Forcing all separately launched profile processes to share a single global database. Independent processes must maintain profile-directory isolation.
- Changing upstream Google OIDC refresh semantics. The failure occurs in the local SQLite application session store, not upstream identity provider revocation.
- Restarting services or performing live database migrations.

## Decisions

### Decision 1: Host Process-Home Binding for Host Auth Provider
The dashboard server resolves its process home (`HERMES_HOME`) at process initialization. When `SelfHostedOIDCProvider` is instantiated for the host dashboard service, its `_session_store` MUST bind to the host process's home directory (`process_home / "dashboard-auth-sessions.db"`), rather than dynamically re-evaluating `get_hermes_home()` during later request handling.
- *Rationale*: A host server process may handle requests routed to different agent profiles via ContextVars. Dynamic evaluation of `get_hermes_home()` inside request context routes DB queries to the profile directory, stranding the host server's session state.
- *Alternatives considered*: Forcing all profiles to use a single hardcoded `~/.hermes/dashboard-auth-sessions.db`. Rejected because it breaks multi-instance isolation when users deliberately launch independent dashboard processes under isolated profiles (`hermes -p coder dashboard`).

### Decision 2: Guard Global Provider Registry Against Request-Scoped Replacement
In `hermes_cli/dashboard_auth/registry.py` and `hermes_cli/plugins.py`, `register_global_provider` and `PluginContext.register_dashboard_auth_provider` SHALL enforce host-ownership gating:
- Once a host-owned provider is registered for the process, subsequent plugin discovery calls originating from routed per-request profiles or profile-scoped contexts SHALL NOT overwrite the registered host provider.
- Forced re-discovery for the host process itself is permitted only when executed in the host process home context.
- *Rationale*: Protects the process-global auth slot from being hijacked by child or profile-scoped plugin scans.
- *Alternatives considered*: Making the auth registry thread-local or ContextVar-scoped. Rejected because FastAPI route handlers and middleware run across threadpool and async loop boundaries where ContextVars can be inconsistently propagated.

### Decision 3: Multi-Profile Process Isolation and Existing DB Compatibility
Independent Hermes dashboard instances launched with `-p <profile_name>` bind to that profile's `HERMES_HOME`. Each process owns its own `dashboard-auth-sessions.db` without interference.
- Existing database files and tables (`sessions`, `kv`) remain fully compatible without schema migrations, table drops, or search fallbacks.
- *Rationale*: Zero risk of data corruption or migration failure during rolling updates.

### Decision 4: Desktop Authoritative Terminal Rejection State Transition
In Electron main and Desktop state management:
- Authoritative terminal rejection (e.g., HTTP 401 from `/auth/native/refresh`, or server response `all_providers_rejected_rt`) transitions that normalized `baseUrl` into an explicit `reauth-required` / `signed-out` state.
- Matching obsolete credentials for that `baseUrl` are cleared atomically.
- The state transition is broadcast to the renderer, setting a terminal auth lock for that connection.
- *Rationale*: Eliminates the gap where credentials are gone but background components still treat the connection as live.

### Decision 5: Background Network Request & Retry Loop Suppression
When a connection is in `reauth-required` / `signed-out` state:
- `store/profile.ts`: `refreshProfiles` immediately returns cached profiles without initiating HTTP/IPC requests or retry loops.
- `use-profile-rail-refresh-on-active.ts`: Focus and visibility listeners defer refreshes rather than firing requests.
- `use-project-tree.ts`: Self-healing retry timers (`ROOT_ERROR_RETRY_MS`) for the affected connection are paused.
- Network errors during this state are absorbed locally without surfacing error toasts or uncaught promise rejections.
- *Rationale*: Directly halts the 29-request 401 storm observed in `desktop.log:24261-24293` and stops uncaught rejections.

### Decision 6: Compare-and-Set Generation Protection & One-Time Resume
In `native-token-refresh.ts` and Desktop state:
- Stale in-flight rejections check the current stored bearer token / generation counter against the rejected bearer. If a newer login or rotation has already occurred, the stale rejection is discarded without clearing or mutating the new session.
- When a confirmed login succeeds, the `reauth-required` / `signed-out` state is cleared.
- Suppressed background sync operations (profile rail, project tree, hermes config) are resumed and coalesced into a single refresh execution.
- *Rationale*: Prevents race conditions between user sign-in and delayed network rejections, and smoothly restores UI state upon authentication.

### Decision 7: Explicit Logout Revocation Semantics Preservation
Explicit user-initiated logout (`hermes:connection-config:oauth-logout` via `oauthLogoutConnectionConfig`):
- MUST continue sending the revocation request to `/auth/native/logout` and clearing local tokens and cookies.
- Transition the connection to `signed-out` cleanly so background loops do not interpret the post-logout state as a broken connection requiring retry storms.
- *Rationale*: Preserves critical security semantics (server-side session invalidation) while avoiding UI noise.

## Threat and Security Analysis

### 1. Auth-Boundary Isolation
- **Boundary**: Multiple agent profiles (root, coder, designer, leader, reviewer) executing on the same host machine.
- **Threat**: Cross-profile session hijacking or contamination. A routed request executed under profile `coder` must not access, modify, or invalidate sessions belonging to the root dashboard or another profile.
- **Mitigation**: The host dashboard auth provider is pinned to the host process home directory. Session lookups and token verifications query only the host process's SQLite database. Profile-scoped plugin discoveries cannot register their session stores into the host registry.

### 2. Credential Storage & No-Secret Logging
- **Threat**: Exposure of refresh tokens, access tokens, or session IDs in log files, telemetry, or error messages.
- **Mitigation**: All logging in `self_hosted/__init__.py`, `registry.py`, `native-token-refresh.ts`, and `profile.ts` must use sanitized tokens (token prefix only, e.g. `hermes-oidc-rt.*`, or length/hash) with zero raw secret logging. Test suites must verify that logs contain no plaintext tokens.

### 3. Stale Rejection vs. Newer Login Race Condition
- **Threat**: A slow network rejection for an expired session arrives after the user has already completed a new login, wiping the user's fresh credentials and locking them in an endless reauth loop.
- **Mitigation**: Compare-and-set (CAS) token verification: `resolveRefresh` and `_clearNativeTokens` check that the rejected token matches the current persisted token before clearing. If storage contains a newer token, the rejection is dropped as a stale no-op.

## Migration and Compatibility
- **Database Compatibility**: Existing `dashboard-auth-sessions.db` SQLite files are preserved in-place without schema migrations, column additions, or destructive alterations.
- **Backward Compatibility**: Clients using cookie sessions, native OAuth tokens, or loopback auth continue to interact with the existing endpoints (`/auth/native/refresh`, `/auth/native/logout`, `/auth/native/authorize`) without breaking changes.
- **Desktop Compatibility**: Older Desktop builds connecting to the stabilized backend continue to work; the backend maintains the standard HTTP 401/200 contract.

## Risks / Trade-offs

- **[Risk: Incomplete Network Suppression]** Some distant UI component might initiate an un-gated HTTP fetch while in signed-out state.
  - *Mitigation*: Centralize network gating at the IPC/transport level (`fetchJsonForBackend` and `JsonRpcGatewayClient`) in addition to hook-level suppression.
- **[Risk: Stale Provider in Multi-Tenant Process]** A single process intentionally hosting multiple isolated dashboard endpoints might need distinct providers.
  - *Mitigation*: Hermes dashboard processes are 1:1 with host processes; multi-tenant isolation is achieved via separate processes (`hermes -p <name> dashboard`), which each maintain their own process home.
- **[Risk: Resumption Flood]** Reconnecting after re-auth might flood the server if all hooks fire simultaneously.
  - *Mitigation*: Coalesce background sync operations into a single refresh execution per data source using existing debouncing patterns.
