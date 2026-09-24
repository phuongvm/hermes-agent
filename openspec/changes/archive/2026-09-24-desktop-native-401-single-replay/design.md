# Design: Native Token 401 Force-Refresh and Single-Replay Recovery

## Context
When using native OAuth bearer tokens, the client tracks client-side expiration (`tokenNeedsRefresh()`). If a token is revoked or invalidated remotely, the gateway returns HTTP 401 before local `expiresAt`.

Without active 401 handling, client requests immediately bubble 401, never calling `/auth/native/refresh`. Subsequent requests see the unexpired token and reuse it, looping endlessly in 401 errors.

## Concurrency and Staggered 401 Challenges
1. Concurrent Requests: Multiple requests sent with the same `initialBearer` may fail around the same time.
2. Staggered Arrival: If Request A's 401 initiates refresh, Request A receives `new-at-1` and replays. If Request B's 401 on `initialBearer` arrives just after Request A's refresh finishes and in-flight tracking is cleared, an unconditional forced refresh would rotate tokens a second time to `new-at-2`. Because many OAuth identity providers invalidate previous access tokens upon rotation, Request A's replay with `new-at-1` would fail.
3. Login / Session Replacement: If a user signs in or switches sessions while an old request is in flight, the old request's 401 must not force-refresh or clear the newly minted credentials.

## Design Decisions

### D1: Generation / Rejected-Bearer Aware Forced Refresh
In `native-token-refresh.ts`:
`ensureNativeAccessToken(baseUrl, { force: true, rejectedBearer })`:
- Loads current `tokens` from `io.load(baseUrl)`.
- If `force` is true and `rejectedBearer` is provided:
  - If `tokens.accessToken && tokens.accessToken !== rejectedBearer`:
    - Storage already has a newer or rotated access token.
    - If `!tokenNeedsRefresh(tokens, now)`, return `tokens.accessToken` immediately without calling `io.refresh`.
    - Do not clear or rotate credentials.

### D2: In-Flight Refresh Coalescence with Bearer Tracking
`inFlight` tracks `{ promise: Promise<string | null>, refreshingBearer?: string }`:
- When a forced refresh begins for `tokens.accessToken`, it records `refreshingBearer = tokens.accessToken`.
- Concurrent callers with matching `rejectedBearer` (or unforced callers) join the in-flight promise.
- Upon completion, `inFlight` entry is deleted.

### D3: Single-Replay Helper with Rejection Awareness
In `native-auth-decisions.ts`:
`executeWithNativeBearerSingleReplay<T>(baseUrl, initialBearer, execute, forceRefresh)`:
- Invokes `execute(initialBearer)`.
- If an authoritative 401 is caught:
  - Calls `forceRefresh(baseUrl, initialBearer)`.
  - If `refreshedAt && refreshedAt !== initialBearer`:
    - Executes `execute(refreshedAt)` once.
  - If replay succeeds, returns result.
  - If replay fails (or refresh returned null / unchanged token), rethrows error.

### D4: Wiring Production Call Sites in `main.ts`
Both `fetchJsonForBackend` and `ipcMain.handle('hermes:api')`:
Pass `(baseUrl, rejectedBearer) => ensureNativeAccessToken(baseUrl, { force: true, rejectedBearer })` to `executeWithNativeBearerSingleReplay`.
