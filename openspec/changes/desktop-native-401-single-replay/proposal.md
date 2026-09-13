# Change: Desktop Native Token 401 Force-Refresh and Single-Replay

## Why
When the remote Hermes gateway revokes or rejects a native bearer access token with an authoritative HTTP 401 before client-side expiration (`expiresAt`), Desktop REST and IPC API requests throw 401 immediately without refreshing via `refreshToken`. Subsequent requests call `ensureNativeAccessToken()`, receive the same locally unexpired but remotely rejected bearer, and cause continuous 401 retry loops.

Furthermore, during concurrent operations, staggered authoritative 401 responses for the same rejected bearer must not trigger repeated serial token rotations after the in-flight refresh promise settles, which would invalidate peers' sole replay. Recovery must be rejected-bearer / generation aware, reusing already-rotated or replaced session tokens without triggering redundant rotations.

## What Changes
1. `apps/desktop/electron/native-token-refresh.ts`:
   - Extend `EnsureNativeAccessTokenOptions` with optional `rejectedBearer?: string`.
   - In `createNativeTokenRefresher`:
     - Generation check: if `force` is true and `rejectedBearer` is provided, check whether `io.load(baseUrl)` already contains a different `accessToken`. If so and the current token is valid, reuse it immediately without forcing another refresh.
     - In-flight coalescence: preserve single in-flight refresh per gateway when the rejected bearer is still current.
     - Stale 401 protection: do not rotate or clear newer session tokens when a stale 401 arrives for an older bearer.
2. `apps/desktop/electron/native-auth-decisions.ts`:
   - `executeWithNativeBearerSingleReplay`: pass `initialBearer` as `rejectedBearer` to `forceRefresh(baseUrl, initialBearer)` on authoritative 401, and replay request with rotated bearer only if `refreshedAt && refreshedAt !== initialBearer`.
3. `apps/desktop/electron/main.ts`:
   - Wire both `fetchJsonForBackend` and `hermes:api` call sites to pass `(baseUrl, rejectedBearer) => ensureNativeAccessToken(baseUrl, { force: true, rejectedBearer })`.
4. Automated Concurrency & Unit Regressions:
   - Add automated staggered-concurrency and newer-login regression tests in `native-token-refresh.test.ts` and `native-auth-decisions.test.ts`.
