## Context

Hermes Desktop is an Electron app talking to a `hermes serve` backend over JSON-RPC/WebSocket. When the backend restarts (gateway or dashboard daemon), a three-vector failure cascade occurs:

1. **Session token invalidation** — the in-memory `_SESSION_TOKEN` is regenerated on boot, invalidating all connected clients with 401.
2. **Thundering herd** — background sync hooks fire burst queries into a dead backend, piling up IPC promises that mass-reject after 60s.
3. **Aggressive reauth latch** — a single transient 401 triggers `isReauthRequired`, opening browser OAuth and a full-screen reauth overlay.

The Desktop app maintains pooled remote backend connections (e.g., `conn:intel-nuc::coder`, `::reviewer`), each of which independently surfaces errors, compounding the pop-up storm.

### Current state

- `hermes_cli/web_server.py`: `_resolve_session_token()` reads `HERMES_DASHBOARD_SESSION_TOKEN` env var, falling back to `secrets.token_urlsafe(32)` — ephemeral in RAM.
- `hermes_cli/dashboard_auth/middleware.py`: `gated_auth_middleware` checks bearer/cookie tokens against `_SESSION_TOKEN`, returning 401 on mismatch regardless of whether server just booted.
- Desktop renderer: background hooks continuously poll `refreshProfiles()`, `refreshSessions()` etc. with no connection-state gate.
- Electron main: a single 401 from a credentialed probe escalates to `makeReauthRequiredError` immediately.

### Key project constraints (from AGENTS.md)

- **Per-conversation prompt caching is sacred** — no mid-conversation system prompt mutations.
- **Non-secret config goes in config.yaml**, not env vars.
- **Session capability is a property of the SESSION, not process env** — the reauth classifier must distinguish transient backend failures from genuine auth revocations regardless of topology (local, remote, cloud).
- **Auth corollary**: "Only a confirmed 401/403 (or an explicitly tagged auth rejection) means reauthentication; timeout, network, malformed-response, and server failures remain connectivity errors."

## Goals / Non-Goals

**Goals:**
- G1: Desktop survives gateway/dashboard restarts with zero pop-up storms or spurious browser re-auth windows.
- G2: Existing session tokens remain valid across daemon restarts when `HERMES_DASHBOARD_SESSION_TOKEN` is not explicitly pinned.
- G3: Background sync hooks are idle during reconnect windows, eliminating thundering-herd promise pile-ups.
- G4: The reauth latch requires sustained evidence of auth failure before escalating, preventing single-transient-401 false positives.
- G5: At most one global re-auth notification across all pooled remote connections.

**Non-Goals:**
- NG1: Changing the OIDC/OAuth flow itself (the existing `SelfHostedOIDCProvider`, `gated_auth_middleware` session verification, and broker logic remain unchanged for authenticated-state operation).
- NG2: Adding new env vars for non-secret configuration (per AGENTS.md policy — the on-disk token file is state, not config).
- NG3: Modifying Desktop behavior when the gateway is healthy and connected (all changes activate only during reconnect transitions).
- NG4: Changing the `hermes update` restart strategy or fleet-wide restart logic.

## Decisions

### D1: Persistent on-disk session token (Backend)
**Choice**: When `HERMES_DASHBOARD_SESSION_TOKEN` is unset, read/write a token file at `$HERMES_HOME/.dashboard_session_token` (chmod 0600 / Windows ACL equivalent) rather than generating an ephemeral random token.

**Alternatives considered**:
- *Always require env var*: Rejected — breaks existing zero-config installs; env var is opt-in for advanced users.
- *Store in config.yaml*: Rejected — the token is a secret/credential, and config.yaml is for non-secret settings per AGENTS.md policy.
- *Store in SQLite state DB*: Rejected — adds coupling to a schema that changes; a standalone file is simpler and independently rotatable.

**Rationale**: The env var takes precedence (backward-compatible). The file persists across restarts, so Desktop clients reconnect seamlessly. File permissions restrict access to the service user.

### D2: Startup grace period — 503 over 401 (Backend)
**Choice**: During the initialization window (before routes and plugins finish loading), the auth middleware returns `503 Service Unavailable` with `Retry-After: 3` header instead of `401 session_expired` for requests arriving with a stale or not-yet-verified token.

**Alternatives considered**:
- *Return 401 with a custom header* (`X-Hermes-Transient: true`): Rejected — non-standard; clients must parse a custom header. 503 is universally understood as "retry later."
- *Queue requests until ready*: Rejected — adds complexity, risks memory pressure under load.

**Rationale**: 503 signals "server not ready" without triggering auth failure handling in any HTTP client. The `Retry-After` header gives clients a concrete backoff target.

### D3: Connection-state-gated background sync (Desktop Renderer)
**Choice**: Background sync hooks (`useBackgroundSync` equivalent, profile rail refresh) check the gateway connection state before issuing refresh calls. When `gatewayState !== 'open'`, calls are suppressed (deferred, not cancelled). A single coalesced retry fires when the state transitions back to `'open'`.

**Alternatives considered**:
- *Exponential backoff without gate*: Rejected — still generates traffic against a dead backend; doesn't prevent the 60s timeout pile-up.
- *Disable background sync entirely during disconnect*: Rejected — too aggressive; a brief reconnect should trigger a refresh once connectivity is restored.

**Rationale**: Gating on state is the narrowest change that eliminates the thundering herd while preserving the "refresh on reconnect" behavior users expect.

### D4: Two-tier error classifier for 401 responses (Electron Main)
**Choice**: Classify 401 responses into two categories:
- **Hard Auth Rejection**: ≥2 consecutive 401 responses within a 15-second window from the same backend, AND gateway state was previously `'open'` (not in startup). Escalates to `isReauthRequired`.
- **Transient Gateway Drop**: A single 401 during a reconnect window, or any 401 when the backend is in startup/connecting state. Treated as a connectivity error — retry, do not escalate.

**Alternatives considered**:
- *Response body inspection* (check `reason` field): Rejected — couples Electron to backend response schema; fragile across versions.
- *Backend-stamped header* (`X-Hermes-Startup: true`): Rejected — the backend may not be ready to stamp headers during its initialization window. The client must be robust independently.

**Rationale**: This aligns with the Desktop AGENTS.md auth corollary: "Only a confirmed 401/403 (or an explicitly tagged auth rejection) means reauthentication; timeout, network, malformed-response, and server failures remain connectivity errors." A debounced multi-probe gate provides that confirmation.

### D5: Single global reauth modal (Electron Main)
**Choice**: A process-wide latch ensures at most one re-auth notification/modal is displayed. Subsequent reauth triggers from other pooled connections are coalesced and resolved by the outcome of the active modal.

**Alternatives considered**:
- *Per-connection reauth modal*: Rejected — this is exactly the current behavior causing the pop-up storm.
- *Silent reauth queue*: Rejected — the user needs to know re-auth is happening, just once.

**Rationale**: Multiple modals for the same underlying cause (backend restart) is noise, not signal. One modal, one user action, all connections benefit.

## Risks / Trade-offs

- **[Risk] Token file permission on Windows** → Mitigation: Use `icacls` equivalent or rely on user-home directory ACL. Document that `$HERMES_HOME` directory-level permissions protect the token file. Test on Windows CI.
- **[Risk] 503 grace period could mask a genuine startup failure** → Mitigation: Grace period is time-bounded (configurable, default 30s from server boot). After the window closes, normal 401 behavior resumes. The grace period ONLY applies when the server has not yet finished its initialization sequence — a flag is cleared once routes are fully mounted.
- **[Risk] 15-second debounce could delay legitimate reauth** → Mitigation: ≥2 consecutive 401s within 15s required before escalating (uniform D4 threshold). The ≥2 consecutive 401 threshold applies uniformly including healthy-connection state, with explicit cross-reference to D4:72 and normative spec (spec.md:35,44-48).
- **[Risk] Coalesced profile store errors could hide non-transient failures** → Mitigation: Error absorption is scoped to the reconnect window only. Errors during stable `'open'` state propagate normally.

## Migration Plan

1. **Backend changes (Tier 1)**: Deploy first. Existing env-var users see zero change. Non-env-var users get a `.dashboard_session_token` file created on first boot — subsequent restarts preserve the token.
2. **Desktop changes (Tiers 2+3)**: Ship in a Desktop release after the backend. The Desktop changes are backward-compatible — older backends without 503 grace period still work; the reauth debounce handles their 401s as transient drops during reconnect.
3. **Rollback**: Remove the token file to revert to ephemeral behavior. Desktop changes are purely additive — removing them restores current (aggressive) reauth behavior.

## Open Questions

- Q1: Should the startup grace period duration be configurable via `config.yaml` (e.g., `dashboard.startup_grace_seconds`)? Current proposal: hardcoded 30s default, config.yaml override.
- Q2: Should the on-disk token file auto-rotate periodically (e.g., every 90 days)? Current proposal: no auto-rotation; user can delete the file to force rotation.
