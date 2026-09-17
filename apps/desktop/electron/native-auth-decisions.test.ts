/**
 * Regression tests for electron/native-auth-decisions.ts — the pure decision
 * seams behind the RFC 8252 native-app auth flow, each of which was a real
 * runtime bug that the mocked flow tests could not catch.
 *
 * Run via the vitest `electron` project (electron/**\/*.test.ts).
 */

import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  executeWithNativeBearerSingleReplay,
  isAuthoritative401,
  normalizeAdvertisedAuthProviders,
  oauthGuardMayHardFail,
  oauthSessionIsLive,
  oauthTicketFailureAuthMessage,
  resolveGatedDownloadAuth,
  resolveJsonBody,
  resolveOauthRestAuth,
  resolveReadinessProbeAuth,
  shouldRotateNativeTokenAfterRejection
} from './native-auth-decisions'
import { createNativeTokenRefresher } from './native-token-refresh'

// --- 1. body encoding (guards the double-JSON.stringify 422) ---

test('resolveJsonBody returns the object unchanged (no pre-stringify)', () => {
  const body = { code: 'abc', code_verifier: 'xyz' }
  const out = resolveJsonBody(body)

  // Must be the SAME object reference / shape — NOT a JSON string. Pre-
  // stringifying here is what produced the gateway 422 "Input should be a
  // valid dictionary" at /auth/native/token.
  assert.equal(typeof out, 'object')
  assert.deepEqual(out, body)
})

test('resolveJsonBody does not stringify — a string stays a string, an object stays an object', () => {
  assert.equal(typeof resolveJsonBody({ a: 1 }), 'object')
  // If a caller ever passes an already-encoded string (the bug), we return it
  // as-is rather than re-wrapping — the contract is "fetchJson owns encoding".
  assert.equal(typeof resolveJsonBody('{"a":1}'), 'string')
})

// --- 2. oauth liveness (guards the needsOauthLogin loop) ---

test('oauthSessionIsLive is true when a native bearer token exists, even with no cookie', () => {
  // The exact bug: native login stores a bearer, sets no cookie. Gating on the
  // cookie alone looped the UI into "not signed in".
  assert.equal(oauthSessionIsLive(true, false), true)
})

test('oauthSessionIsLive is true when a live cookie exists with no native token', () => {
  assert.equal(oauthSessionIsLive(false, true), true)
})

test('oauthSessionIsLive is true when both are present', () => {
  assert.equal(oauthSessionIsLive(true, true), true)
})

test('oauthSessionIsLive is false only when neither is present', () => {
  assert.equal(oauthSessionIsLive(false, false), false)
})

// --- 3. REST auth selection (guards the 401 no_cookie) ---

test('resolveOauthRestAuth prefers the native bearer when a token is present', () => {
  const auth = resolveOauthRestAuth('bearer-token-123')

  assert.deepEqual(auth, { kind: 'bearer', token: 'bearer-token-123' })
})

test('resolveOauthRestAuth falls back to cookie when there is no native token', () => {
  assert.deepEqual(resolveOauthRestAuth(null), { kind: 'cookie' })
  assert.deepEqual(resolveOauthRestAuth(undefined), { kind: 'cookie' })
  // Empty string is not a usable bearer — must fall back, not send "Bearer ".
  assert.deepEqual(resolveOauthRestAuth(''), { kind: 'cookie' })
})

// --- 4. readiness-probe auth (guards the credential-free 401 boot loop) ---

test('resolveReadinessProbeAuth reuses the oauth bearer-vs-cookie choice', () => {
  assert.deepEqual(resolveReadinessProbeAuth('oauth', 'native-at'), { kind: 'bearer', token: 'native-at' })
  assert.deepEqual(resolveReadinessProbeAuth('oauth', null), { kind: 'cookie' })
  assert.deepEqual(resolveReadinessProbeAuth('oauth', ''), { kind: 'cookie' })
})

test('resolveReadinessProbeAuth sends the session token for a token gateway', () => {
  assert.deepEqual(resolveReadinessProbeAuth('token', null, 'session-token'), {
    kind: 'token',
    token: 'session-token'
  })
  assert.deepEqual(resolveReadinessProbeAuth('token', null, null), { kind: 'token', token: null })
})

test('resolveReadinessProbeAuth stays public for local and unknown modes', () => {
  // A loopback backend has no gate; sending credentials it never issued is
  // meaningless, and an unknown mode must not invent a credential.
  assert.deepEqual(resolveReadinessProbeAuth('local', 'native-at', 'session-token'), { kind: 'public' })
  assert.deepEqual(resolveReadinessProbeAuth(undefined, 'native-at', 'session-token'), { kind: 'public' })
  assert.deepEqual(resolveReadinessProbeAuth('something-new', null, null), { kind: 'public' })
})

// --- 5. oauth guard vs password gateways (guards the false "not signed in") ---

test('oauthGuardMayHardFail is false only when EVERY provider is password-based', () => {
  assert.equal(oauthGuardMayHardFail([{ name: 'basic', supportsPassword: true }]), false)
  assert.equal(
    oauthGuardMayHardFail([
      { name: 'basic', supportsPassword: true },
      { name: 'ldap', supportsPassword: true }
    ]),
    false
  )
})

test('oauthGuardMayHardFail keeps the strict guard for oauth and mixed deployments', () => {
  assert.equal(oauthGuardMayHardFail([{ name: 'nous', supportsPassword: false }]), true)
  assert.equal(
    oauthGuardMayHardFail([
      { name: 'nous', supportsPassword: false },
      { name: 'basic', supportsPassword: true }
    ]),
    true
  )
})

test('oauthGuardMayHardFail keeps the strict guard when the list is unusable', () => {
  // Backends predating /api/auth/providers, or an unreachable probe, must not
  // silently weaken the guard.
  assert.equal(oauthGuardMayHardFail([]), true)
  assert.equal(oauthGuardMayHardFail(null), true)
  assert.equal(oauthGuardMayHardFail(undefined), true)
  assert.equal(oauthGuardMayHardFail('nonsense' as any), true)
  assert.equal(oauthGuardMayHardFail([{ supportsPassword: true }]), true)
})

test('oauthGuardMayHardFail treats status-shaped string basic as password-only', () => {
  assert.equal(oauthGuardMayHardFail(['basic'] as any), false)
  assert.equal(oauthGuardMayHardFail([' basic '] as any), false)
})

test('oauthGuardMayHardFail keeps the strict guard for string oauth providers', () => {
  assert.equal(oauthGuardMayHardFail(['nous'] as any), true)
  assert.equal(oauthGuardMayHardFail(['nous', 'basic'] as any), true)
})

test('normalizeAdvertisedAuthProviders maps snake_case supports_password', () => {
  assert.deepEqual(normalizeAdvertisedAuthProviders([{ name: 'basic', supports_password: true }]), [
    { name: 'basic', supportsPassword: true }
  ])
})

test('oauthTicketFailureAuthMessage is expired only with a decryptable native session', () => {
  assert.match(oauthTicketFailureAuthMessage(true), /session has expired/)
  assert.match(oauthTicketFailureAuthMessage(false), /not signed in/)
})

// --- 6. gated download auth (guards the Files-panel 401 on cookieless native) ---

test('resolveGatedDownloadAuth matches oauth REST: bearer first, then cookie', () => {
  assert.deepEqual(resolveGatedDownloadAuth('oauth', 'native-at'), { kind: 'bearer', token: 'native-at' })
  assert.deepEqual(resolveGatedDownloadAuth('oauth', null), { kind: 'cookie' })
  assert.deepEqual(resolveGatedDownloadAuth('oauth', ''), { kind: 'cookie' })
})

test('resolveGatedDownloadAuth uses the session token for token and local modes', () => {
  assert.deepEqual(resolveGatedDownloadAuth('token', 'native-at', 'session-token'), {
    kind: 'token',
    token: 'session-token'
  })
  assert.deepEqual(resolveGatedDownloadAuth('local', null, 'sess'), { kind: 'token', token: 'sess' })
  assert.deepEqual(resolveGatedDownloadAuth(undefined, null, null), { kind: 'token', token: null })
})

// --- 7. native bearer single-replay contract on authoritative 401 ---

test('isAuthoritative401 detects statusCode 401 and 401: error messages', () => {
  assert.equal(isAuthoritative401({ statusCode: 401 }), true)
  assert.equal(isAuthoritative401(Object.assign(new Error('unauthorized'), { statusCode: 401 })), true)
  assert.equal(isAuthoritative401(new Error('401: Unauthorized')), true)
  assert.equal(isAuthoritative401({ statusCode: 403 }), false)
  assert.equal(isAuthoritative401({ statusCode: 500 }), false)
  assert.equal(isAuthoritative401(new Error('500: Internal Server Error')), false)
  assert.equal(isAuthoritative401(null), false)
  assert.equal(isAuthoritative401(undefined), false)
})

test('executeWithNativeBearerSingleReplay returns on first try when request succeeds', async () => {
  let refreshCalled = 0

  const result = await executeWithNativeBearerSingleReplay(
    'https://gateway',
    'initial-token',
    async bearer => `ok-${bearer}`,
    async () => {
      refreshCalled++

      return 'refreshed-token'
    }
  )

  assert.equal(result, 'ok-initial-token')
  assert.equal(refreshCalled, 0)
})

test('executeWithNativeBearerSingleReplay force-refreshes and replays once on authoritative 401', async () => {
  const calls: string[] = []
  let refreshCalled = 0

  const result = await executeWithNativeBearerSingleReplay(
    'https://gateway',
    'initial-token',
    async bearer => {
      calls.push(bearer)

      if (bearer === 'initial-token') {
        const err: any = new Error('401: Unauthorized')
        err.statusCode = 401
        throw err
      }

      return `ok-${bearer}`
    },
    async baseUrl => {
      refreshCalled++
      assert.equal(baseUrl, 'https://gateway')

      return 'refreshed-token'
    }
  )

  assert.equal(result, 'ok-refreshed-token')
  assert.deepEqual(calls, ['initial-token', 'refreshed-token'])
  assert.equal(refreshCalled, 1)
})

test('executeWithNativeBearerSingleReplay bubbles 401 without replaying if refresh returns null', async () => {
  let refreshCalled = 0
  const calls: string[] = []

  await assert.rejects(
    () =>
      executeWithNativeBearerSingleReplay(
        'https://gateway',
        'initial-token',
        async bearer => {
          calls.push(bearer)
          const err: any = new Error('401: Unauthorized')
          err.statusCode = 401
          throw err
        },
        async () => {
          refreshCalled++

          return null
        }
      ),
    /401: Unauthorized/
  )

  assert.deepEqual(calls, ['initial-token'])
  assert.equal(refreshCalled, 1)
})

test('executeWithNativeBearerSingleReplay performs at most ONE replay if replayed request also 401s', async () => {
  let refreshCalled = 0
  const calls: string[] = []

  await assert.rejects(
    () =>
      executeWithNativeBearerSingleReplay(
        'https://gateway',
        'initial-token',
        async bearer => {
          calls.push(bearer)
          const err: any = new Error(`401: Rejection for ${bearer}`)
          err.statusCode = 401
          throw err
        },
        async () => {
          refreshCalled++

          return 'refreshed-token'
        }
      ),
    /401: Rejection for refreshed-token/
  )

  assert.deepEqual(calls, ['initial-token', 'refreshed-token'])
  assert.equal(refreshCalled, 1)
})

test('executeWithNativeBearerSingleReplay does not refresh on non-401 errors', async () => {
  let refreshCalled = 0

  await assert.rejects(
    () =>
      executeWithNativeBearerSingleReplay(
        'https://gateway',
        'initial-token',
        async () => {
          const err: any = new Error('500: Internal Server Error')
          err.statusCode = 500
          throw err
        },
        async () => {
          refreshCalled++

          return 'refreshed-token'
        }
      ),
    /500: Internal Server Error/
  )

  assert.equal(refreshCalled, 0)
})

test('executeWithNativeBearerSingleReplay passes rejectedBearer to forceRefresh on 401', async () => {
  let passedRejectedBearer = ''
  await executeWithNativeBearerSingleReplay(
    'https://gateway',
    'my-stale-bearer',
    async bearer => {
      if (bearer === 'my-stale-bearer') {
        const err: any = new Error('401: Unauthorized')
        err.statusCode = 401
        throw err
      }

      return 'ok'
    },
    async (baseUrl, rejectedBearer) => {
      passedRejectedBearer = rejectedBearer

      return 'new-bearer'
    }
  )
  assert.equal(passedRejectedBearer, 'my-stale-bearer')
})

test('executeWithNativeBearerSingleReplay does not replay if refresh returns the same rejected bearer', async () => {
  const calls: string[] = []
  await assert.rejects(
    () =>
      executeWithNativeBearerSingleReplay(
        'https://gateway',
        'stale-token',
        async bearer => {
          calls.push(bearer)
          const err: any = new Error('401: Unauthorized')
          err.statusCode = 401
          throw err
        },
        async () => 'stale-token'
      ),
    /401: Unauthorized/
  )
  assert.deepEqual(calls, ['stale-token'])
})

test('staggered authoritative 401s for the same rejected bearer share single rotation and both replay successfully', async () => {
  let tokens: any = {
    accessToken: 'old-at',
    refreshToken: 'stable-rt',
    expiresAt: 9999999999,
    provider: 'self-hosted',
    userId: 'user'
  }

  let refreshes = 0
  let releaseSecondOld401!: () => void
  let releaseFirstReplay!: () => void
  let signalFirstReplayStarted!: () => void

  const secondOld401 = new Promise<void>(resolve => {
    releaseSecondOld401 = resolve
  })

  const firstReplayGate = new Promise<void>(resolve => {
    releaseFirstReplay = resolve
  })

  const firstReplayStarted = new Promise<void>(resolve => {
    signalFirstReplayStarted = resolve
  })

  const ensure = createNativeTokenRefresher({
    load: () => tokens,
    store: (_baseUrl, next) => {
      tokens = next
    },
    clear: () => {
      tokens = null
    },
    refresh: async () => {
      refreshes += 1

      return {
        access_token: `new-at-${refreshes}`,
        refresh_token: 'stable-rt',
        expires_at: 9999999999,
        provider: 'self-hosted',
        user_id: 'user'
      }
    },
    now: () => 1000
  })

  function unauthorized(label: string): Error {
    return Object.assign(new Error(`401: ${label}`), { statusCode: 401 })
  }

  const first = executeWithNativeBearerSingleReplay(
    'https://gateway.example',
    'old-at',
    async bearer => {
      if (bearer === 'old-at') {throw unauthorized('old bearer rejected')}
      signalFirstReplayStarted()
      await firstReplayGate

      if (bearer !== tokens?.accessToken) {throw unauthorized('replay bearer invalidated by later refresh')}

      return bearer
    },
    (baseUrl, rejectedBearer) => ensure(baseUrl, { force: true, rejectedBearer })
  )

  const second = executeWithNativeBearerSingleReplay(
    'https://gateway.example',
    'old-at',
    async bearer => {
      if (bearer === 'old-at') {
        await secondOld401
        throw unauthorized('delayed old bearer rejected')
      }

      if (bearer !== tokens?.accessToken) {throw unauthorized('unexpected stale second replay')}

      return bearer
    },
    (baseUrl, rejectedBearer) => ensure(baseUrl, { force: true, rejectedBearer })
  )

  await firstReplayStarted
  releaseSecondOld401()
  const secondResult = await second
  releaseFirstReplay()
  const firstResult = await first

  assert.equal(firstResult, 'new-at-1')
  assert.equal(secondResult, 'new-at-1')
  assert.equal(refreshes, 1)
  assert.equal(tokens?.accessToken, 'new-at-1')
})

// --- 8. forced native rotation after a bearer rejection (#95701) ---

test('shouldRotateNativeTokenAfterRejection: only a structured 401 earns the one forced refresh', () => {
  // The gate never rotates a native bearer server-side, so a 401 on a
  // locally-unexpired access token is ambiguous until /auth/native/refresh
  // has run once.
  assert.equal(
    shouldRotateNativeTokenAfterRejection(Object.assign(new Error('401: expired'), { statusCode: 401 })),
    true
  )
  assert.equal(shouldRotateNativeTokenAfterRejection({ statusCode: 401 }), true)
})

test('shouldRotateNativeTokenAfterRejection: 403, 5xx, transport, and anonymous errors never rotate', () => {
  // 403 is a policy refusal for an identity the gate recognized — a fresh
  // bearer for the same identity cannot change it.
  assert.equal(
    shouldRotateNativeTokenAfterRejection(Object.assign(new Error('403: forbidden'), { statusCode: 403 })),
    false
  )
  assert.equal(shouldRotateNativeTokenAfterRejection(Object.assign(new Error('503: down'), { statusCode: 503 })), false)
  assert.equal(shouldRotateNativeTokenAfterRejection(Object.assign(new Error('reset'), { code: 'ECONNRESET' })), false)
  // The pre-fix fetchJson shape: a "401: ..." message with no statusCode says
  // nothing structured about the credential and must not trigger rotation.
  assert.equal(shouldRotateNativeTokenAfterRejection(new Error('401: {"error":"session_expired"}')), false)
  assert.equal(shouldRotateNativeTokenAfterRejection(null), false)
  assert.equal(shouldRotateNativeTokenAfterRejection(undefined), false)
  assert.equal(shouldRotateNativeTokenAfterRejection('401'), false)
})
