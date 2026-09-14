# Change: Stabilize Dashboard Auth Runtime Ownership and Terminal Signed-Out Behavior

## Why
When remote Desktop sessions encounter terminal auth rejection, a process-global backend auth provider replacement bug causes valid SQLite application sessions to be dropped, while Desktop background polling loops enter unauthenticated 401 retry storms without entering an explicit signed-out state. This change stabilizes backend dashboard-auth provider ownership to the process home and establishes Desktop terminal-auth suspension and recovery to eliminate auth storms and false re-identification cascades.

## What Changes
This change addresses the defect across two focused implementation micro-lanes:

1. **Lane 1: Backend Host-Auth Provider Ownership**
   - Bind `SelfHostedOIDCProvider` and its session store immutably to the host dashboard process home (`HERMES_HOME` resolved at server boot), preventing request-scoped or routed profile overrides from altering session database paths.
   - Guard `register_global_provider` and `PluginContext.register_dashboard_auth_provider` so routed per-request profile rediscovery cannot replace the active host-owned provider with a profile-scoped or request-scoped instance.
   - Preserve process-level multi-profile isolation: dashboards explicitly launched under a named profile (e.g., `hermes -p <name> dashboard`) remain bound to that process profile home, without forcing independent processes to share a single global database.
   - Maintain strict backward compatibility for existing `dashboard-auth-sessions.db` databases without destructive schema changes or search fallbacks.

2. **Lane 2: Desktop Terminal-Auth Suspension and Recovery**
   - Atomically transition the affected normalized base URL into an explicit `reauth-required` / `signed-out` state upon authoritative terminal refresh rejection (e.g., HTTP 401 from `/auth/native/refresh`).
   - Suppress background network activity for the affected base URL: pause profile rail refreshes (`useProfileRailRefreshOnActive`, `refreshProfiles`), project tree self-healing polling loops (`useProjectTree`), and configuration checks, failing fast or absorbing errors locally without retry timers, error toasts, or uncaught promise rejections.
   - Enforce compare-and-set / generation awareness so a stale in-flight rejection cannot clear credentials written by a newer successful login.
   - Reset the terminal signed-out state upon confirmed sign-in, resuming and coalescing deferred background synchronization exactly once.
   - Preserve explicit user logout (`oauthLogoutConnectionConfig`) semantics: retain server session revocation and local credential deletion while avoiding unauthenticated background polling storms.

## Capabilities

### Modified Capabilities
- `dashboard-auth`: Bind host dashboard auth provider and session database to the host process home across routed profile rediscovery, maintaining process-level isolation and protecting global registrations against request-scoped replacement.
- `desktop-reconnect-resilience`: Transition Desktop into explicit signed-out / reauth-required state on authoritative terminal auth rejection, suppressing background network requests and uncaught rejections while preserving generation-safe newer logins and explicit logout semantics.

## Impact
- **Backend Components**:
  - `plugins/dashboard_auth/self_hosted/__init__.py`: Anchor `_session_store` to host process home.
  - `hermes_cli/plugins.py`: Guard `register_dashboard_auth_provider` against request-scoped host replacement.
  - `hermes_cli/dashboard_auth/registry.py`: Protect global provider registration against profile-scoped overwrites.
- **Desktop Components**:
  - `apps/desktop/electron/native-token-refresh.ts`: Authoritative terminal rejection state transition and generation-safe clear.
  - `apps/desktop/src/store/profile.ts`: Gate background profile refreshes during terminal signed-out state.
  - `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts`: Suppress active rail refreshes while signed out.
  - `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts`: Suppress self-healing retry timers while signed out.
- **Dependencies & APIs**: No external dependencies or breaking public API changes. Internal auth provider registration and desktop connection state contracts are tightened.
