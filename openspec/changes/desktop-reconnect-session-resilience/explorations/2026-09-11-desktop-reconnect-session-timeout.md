# OpenSpec Exploration: Hermes Desktop Reconnect Session Timeout & Pop-up Storm

**Date**: 2026-09-11  
**Status**: Exploration & Architecture Proposal Complete  
**Project**: `hermes-agent` (`O:\workspaces\oss\hermes-agent`)  
**Scope**: Hermes Desktop Electron/Renderer (`apps/desktop`), Dashboard Auth Middleware (`hermes_cli/dashboard_auth`), and Web Server Session Lifecycle  

---

## 1. Executive Summary & Incident Manifest

### 1.1 Problem Statement
When the Hermes Gateway or Dashboard service is restarted (e.g. during configuration updates, background daemon maintenance, or environment reloading), users operating Hermes Desktop observe:
1. A cascade of intrusive pop-ups and warning toasts reporting **"session timeout"** or **"remote gateway session has expired"**.
2. Spurious browser pop-ups attempting to trigger local OAuth loopback sign-in (`127.0.0.1:<port>`).
3. Stalled IPC promises in the Desktop renderer timing out after **60,000ms** (`STARTUP_REQUEST_TIMEOUT_MS`), temporarily freezing or degrading UI panes.

### 1.2 Quantitative Log Evidence (`desktop.log`)
Investigation of runtime logs at `O:\workspaces\_config\agent4070\hermes\logs\desktop.log` reveals concrete timeline evidence of the failure cascade:

```log
[2026-09-11T02:31:44.199Z] [hermes] [renderer console:main] [profiles] refreshProfiles failed after 3 attempt(s): Error: Error invoking remote method 'hermes:api': Error: 401: {"error":"session_expired","detail":"Unauthorized","reason":"invalid_or_expired_session","login_url":"/login"}
[2026-09-11T02:31:47.233Z] [hermes] [renderer console:main] Uncaught (in promise) Error: Error invoking remote method 'hermes:api': Error: 401: {"error":"session_expired","detail":"Unauthorized","reason":"invalid_or_expired_session","login_url":"/login"}
[2026-09-11T02:35:22.788Z] [hermes] [renderer console:main] [profiles] refreshProfiles failed after 3 attempt(s): Error: Error invoking remote method 'hermes:api': Error: 401: {"error":"unauthenticated","detail":"Unauthorized","reason":"no_cookie","login_url":"/login"}
[2026-09-11T02:38:39.205Z] [hermes] [native-oauth] loopback listening on 127.0.0.1:59342; opening system browser
[2026-09-11T02:43:00.412Z] [hermes] Reaping idle profile backend "conn:intel-nuc::reviewer" (idle > 600s)
[2026-09-11T02:43:00.412Z] [hermes] Reaping idle profile backend "conn:intel-nuc::researcher" (idle > 600s)
[2026-09-11T03:34:20.048Z] [hermes] Pooled remote backend "conn:intel-nuc::default" failed its dispatch probe (Timed out connecting to Hermes backend after 2500ms); reconnecting on demand.
[2026-09-11T03:50:53.945Z] [hermes] [renderer console:main] Uncaught (in promise) Error: Error invoking remote method 'hermes:api': Error: Timed out connecting to Hermes backend after 60000ms
```

---

## 2. Root Cause Analysis (Three Compounding Vectors)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ROOT CAUSE CASCADE ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│ [Gateway/Dashboard Restart]                                                 │
│               │                                                             │
│       ┌───────┴───────────────────────────────┐                             │
│       ▼                                       ▼                             │
│  Vector 1: Backend Session Invalidation  Vector 2: Thundering Herd Hooks    │
│  - In-memory token re-minted             - useProfileRailRefreshOnActive    │
│  - SQLite session disconnect             - useBackgroundSync                │
│  - Emits 401 "session_expired"           - 60s STARTUP_REQUEST_TIMEOUT_MS   │
│       │                                       │                             │
│       └───────────────────┬───────────────────┘                             │
│                           ▼                                                 │
│       Vector 3: Multi-Connection Profile Pool Exhaustion                    │
│       - Idle reap (600s) & dispatch probes fail (2500ms)                    │
│       - Electron Main latches isReauthRequired                              │
│       - Renderer triggers Multiple Pop-up Overlays & Loopback Browser       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Vector 1: Ephemeral In-Memory `_SESSION_TOKEN` & OIDC Invalidation
* **Location**: `hermes_cli/web_server.py:294-298`
  ```python
  def _resolve_session_token() -> str:
      return os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN") or secrets.token_urlsafe(32)

  _SESSION_TOKEN = _resolve_session_token()
  ```
* **Failure Mechanism**: When the dashboard daemon restarts without a pinned `HERMES_DASHBOARD_SESSION_TOKEN` in its service environment, a fresh cryptographically random token is generated in RAM. Every in-flight or reconnecting REST and WebSocket call presenting the previously issued token is rejected by `gated_auth_middleware` with HTTP 401 (`invalid_or_expired_session` or `no_cookie`).
* **Desktop Misclassification**: In Electron (`apps/desktop/electron/backend-health.ts:282`), a 401 from a credentialed probe is classified as `isReauthRequiredError = true`. The desktop assumes the user's login has irrevocably expired rather than recognizing a transient daemon restart, immediately latching `remoteReauthFailure` and launching the external system browser via `native-oauth-login.ts`.

### Vector 2: Thundering Herd from Background Sync Hooks
* **Location**: `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts`, `apps/desktop/src/app/contrib/hooks/use-background-sync.ts:634`, and `apps/desktop/src/store/profile.ts:90`
* **Failure Mechanism**: 
  1. Desktop hooks listen to window focus, document visibility, and WebSocket connection states.
  2. While the backend takes 10–30s to initialize routes and load plugins, Desktop issues concurrent burst queries: `refreshProfiles()`, `refreshSessions()`, `refreshHermesConfig()`, `refreshCurrentModel()`.
  3. The request timeout for startup and profile discovery is set to `STARTUP_REQUEST_TIMEOUT_MS = 60_000ms`.
  4. Multiple requests pile up in the IPC handler `hermes:api`. When the socket drops or the 60s ceiling expires, promises reject simultaneously, generating an avalanche of uncaught promise rejections and pop-up toasts.

### Vector 3: Cascading Failures in Pooled Remote Backends
* **Location**: `apps/desktop/electron/main.ts:16561` (`dispatchApiRequestRoute`)
* **Failure Mechanism**: The desktop maintains dedicated backend connections for remote profiles (e.g., `conn:intel-nuc::coder`, `reviewer`, `qa`, `leader`). When the remote host or gateway restarts, dispatch probes fail (`2500ms timeout`), trigger idle reaping (`> 600s`), and each profile's independent failure produces separate error alerts across the active workspace.

---

## 3. Architecture Proposal & Remediation Strategy

To achieve zero-downtime resilience and prevent spurious pop-ups during gateway/dashboard restarts, we propose a 3-tier architectural fix:

### Tier 1: Deterministic Backend Session Token & Reconnect Grace Period (Backend)
1. **Durable Token Resolution**: If `HERMES_DASHBOARD_SESSION_TOKEN` is unset in the environment, fallback to reading/writing a persistent state file (`$HERMES_HOME/.dashboard_session_token`, chmod 0600) rather than generating an ephemeral random token on each boot.
2. **Transient Restart Grace Period**: In `gated_auth_middleware`, differentiate between a stale token during service startup cooldown (return `503 Service Unavailable` with `Retry-After: 3`) and an actual invalid token. Returning 503 causes clients to back off and retry, rather than hard-failing to 401 re-auth.

### Tier 2: Desktop Reconnection State Gate & Silent Backoff (Desktop Renderer)
1. **Gate Background Calls on Connection Readiness**: In `useBackgroundSync` and `useProfileRailRefreshOnActive`, halt `refreshProfiles()` and `refreshSessions()` while `gatewayState !== 'open'` or when `isReconnecting === true`.
2. **Coalesced Error Handling in `refreshProfiles()`**: Ensure `store/profile.ts` absorbs network errors during reconnect transitions silently, maintaining cached profile state rather than logging uncaught promise rejections to the UI.

### Tier 3: Hardened Electron Auth Latch & Probe Classification (Electron Main)
1. **Distinguish Transient 401 from Revocation**: In `electron/backend-health.ts`, do not immediately escalate a single 401 during a reconnection window to `makeReauthRequiredError`. Require at least 2 consecutive failed attempts over 15 seconds before opening the browser loopback listener.
2. **Debounce Remote Reauth Popups**: Ensure only ONE global re-auth notification or modal can be displayed across all pooled remote connections.

---

## 4. OpenSpec Delta Specifications (Draft)

### Requirement: Service Session Persistence Across Daemon Restarts
```markdown
#### Scenario: Gateway daemon restarts with active desktop client
- GIVEN a running Hermes Desktop connected via OAuth/remote session
- WHEN the gateway or dashboard process restarts
- THEN the dashboard shall load the persisted session token from disk
- AND the desktop's reconnection attempts shall succeed without HTTP 401 invalidation
```

### Requirement: Desktop Background Sync Suppression During Disconnect
```markdown
#### Scenario: Desktop window regains focus while backend is restarting
- GIVEN the desktop app is focused during a backend reboot (gatewayState == 'connecting')
- WHEN useProfileRailRefreshOnActive triggers
- THEN it shall defer execution until gatewayState transitions to 'open'
- AND no 60000ms timeout error pop-up shall be displayed to the user
```

---

## 5. Team Execution Plan (Kanban Session Director Blueprint)

| Milestone | Task | Assignee | Role / Scope |
| :--- | :--- | :---: | :--- |
| **M1: Exploration & OpenSpec Proposal** | Formalize OpenSpec change `desktop-reconnect-session-resilience` | `@leader` | Lead / Spec Authoring |
| **M2: Backend Token Persistence** | Implement `$HERMES_HOME/.dashboard_session_token` & 503 grace period | `@coder` | Backend (`hermes_cli/web_server.py`, `middleware.py`) |
| **M3: Desktop Reconnect State Gate** | Patch `useBackgroundSync`, `store/profile.ts`, & `backend-health.ts` | `@coder` / `@designer` | Desktop UI/Electron |
| **M4: Independent Verification & Testing** | E2E test restart cycle without 401 or popups; full regression | `@reviewer` | Review & Verification |
| **M5: QA Compliance Audit** | Verify Zero-Trust acceptance criteria & OpenSpec completeness | `@default` (QA) | QA Sign-off |
