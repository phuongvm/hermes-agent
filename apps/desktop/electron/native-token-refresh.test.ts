import { describe, expect, it, vi } from 'vitest'

import { type NativeTokenSet } from './native-oauth'
import { createNativeTokenRefresher } from './native-token-refresh'

function fixture() {
  let now = 1000

  let tokens: NativeTokenSet | null = {
    accessToken: 'old-at',
    refreshToken: 'stable-rt',
    expiresAt: 1040,
    provider: 'self-hosted',
    userId: 'user'
  }

  const refresh = vi.fn(async () => ({
    access_token: 'new-at',
    refresh_token: 'new-rt',
    expires_at: 1900,
    provider: 'self-hosted',
    user_id: 'user'
  }))

  const clear = vi.fn(() => {
    tokens = null
  })

  const store = vi.fn((_baseUrl: string, next: NativeTokenSet) => {
    tokens = next
  })

  const markSignedOut = vi.fn()
  const clearSignedOut = vi.fn()

  const ensure = createNativeTokenRefresher({
    load: () => tokens,
    store,
    clear,
    refresh,
    now: () => now,
    markSignedOut,
    clearSignedOut
  })

  return {
    ensure,
    refresh,
    clear,
    store,
    markSignedOut,
    clearSignedOut,
    get: () => tokens,
    set: (next: NativeTokenSet | null) => {
      tokens = next
    },
    setNow: (next: number) => {
      now = next
    }
  }
}

describe('native token renewal', () => {
  it('shares one refresh across simultaneous REST and WebSocket requests', async () => {
    const context = fixture()
    const results = await Promise.all(Array.from({ length: 20 }, () => context.ensure('https://gateway.example')))
    expect(context.refresh).toHaveBeenCalledTimes(1)
    expect(results).toEqual(Array(20).fill('new-at'))
    expect(context.store).toHaveBeenCalledTimes(1)
    expect(context.get()?.refreshToken).toBe('new-rt')
  })

  it('does not discard an unrefreshable token a minute before its actual expiry', async () => {
    const context = fixture()
    context.set({ ...context.get()!, refreshToken: '' })
    expect(await context.ensure('gateway')).toBe('old-at')
    expect(context.clear).not.toHaveBeenCalled()
    context.setNow(1040)
    expect(await context.ensure('gateway')).toBeNull()
    expect(context.clear).toHaveBeenCalledTimes(1)
  })

  it.each([503, 429, 500])(
    'preserves credentials and surfaces transient HTTP %s instead of cookie fallback',
    async statusCode => {
      const context = fixture()
      context.refresh.mockRejectedValueOnce(Object.assign(new Error('temporary'), { statusCode }))
      await expect(context.ensure('gateway')).rejects.toThrow('temporary')
      expect(context.get()?.refreshToken).toBe('stable-rt')
      expect(context.clear).not.toHaveBeenCalled()
      expect(await context.ensure('gateway')).toBe('new-at')
      expect(context.refresh).toHaveBeenCalledTimes(2)
    }
  )

  it('keeps credentials on network timeout', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(new Error('timeout'))
    await expect(context.ensure('gateway')).rejects.toThrow('timeout')
    expect(context.get()?.accessToken).toBe('old-at')
  })

  it('clears only on a terminal refresh rejection', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('expired'), { statusCode: 401 }))
    expect(await context.ensure('gateway')).toBeNull()
    expect(context.clear).toHaveBeenCalledTimes(1)
  })

  it('clears on a terminal 401 error formatted as a standard Error without statusCode property', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(new Error('401: {"detail":"Unauthorized"}'))
    expect(await context.ensure('gateway')).toBeNull()
    expect(context.clear).toHaveBeenCalledTimes(1)
  })

  it('does not resurrect a session logged out while refresh was in flight', async () => {
    const context = fixture()
    const pending = context.ensure('gateway')
    context.set(null)
    expect(await pending).toBeNull()
    expect(context.store).not.toHaveBeenCalled()
  })

  it('does not clear a fresh login after an old refresh is rejected', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('expired'), { statusCode: 401 }))
    const pending = context.ensure('gateway')
    context.set({ ...context.get()!, accessToken: 'fresh-login', refreshToken: 'fresh-rt' })
    expect(await pending).toBe('fresh-login')
    expect(context.clear).not.toHaveBeenCalled()
  })

  it('does not overwrite a fresh login with the old refresh result', async () => {
    const context = fixture()
    const pending = context.ensure('gateway')
    context.set({ ...context.get()!, accessToken: 'fresh-login', refreshToken: 'fresh-rt' })
    expect(await pending).toBe('fresh-login')
    expect(context.store).not.toHaveBeenCalled()
  })

  it('preserves a non-rotating refresh credential when the response omits it', async () => {
    const context = fixture()
    context.refresh.mockResolvedValueOnce({
      access_token: 'new-at',
      refresh_token: '',
      expires_at: 1900,
      provider: 'self-hosted',
      user_id: 'user'
    })
    expect(await context.ensure('gateway')).toBe('new-at')
    expect(context.get()?.refreshToken).toBe('stable-rt')
  })

  it('does not merge refresh operations for different gateways', async () => {
    const context = fixture()
    await Promise.all([context.ensure('gateway-a'), context.ensure('gateway-b')])
    expect(context.refresh).toHaveBeenCalledTimes(2)
  })

  it('bypasses local expiresAt on forced refresh and mints new token via refreshToken', async () => {
    const context = fixture()
    context.set({ ...context.get()!, expiresAt: 5000 })
    expect(await context.ensure('gateway')).toBe('old-at')
    expect(context.refresh).not.toHaveBeenCalled()

    const refreshed = await context.ensure('gateway', { force: true })
    expect(refreshed).toBe('new-at')
    expect(context.refresh).toHaveBeenCalledTimes(1)
    expect(context.store).toHaveBeenCalledTimes(1)
    expect(context.get()?.accessToken).toBe('new-at')
  })

  it('coalesces concurrent forced refresh callers into a single in-flight refresh promise', async () => {
    const context = fixture()
    context.set({ ...context.get()!, expiresAt: 5000 })

    const results = await Promise.all(
      Array.from({ length: 10 }, () => context.ensure('gateway', { force: true }))
    )

    expect(context.refresh).toHaveBeenCalledTimes(1)
    expect(results).toEqual(Array(10).fill('new-at'))
    expect(context.store).toHaveBeenCalledTimes(1)
    expect(context.get()?.accessToken).toBe('new-at')
  })

  it('does not overwrite newer tokens with stale in-flight forced refresh results', async () => {
    const context = fixture()
    context.set({ ...context.get()!, expiresAt: 5000 })

    const pending = context.ensure('gateway', { force: true })
    context.set({ ...context.get()!, accessToken: 'newer-at', refreshToken: 'newer-rt' })

    expect(await pending).toBe('newer-at')
    expect(context.store).not.toHaveBeenCalled()
    expect(context.get()?.accessToken).toBe('newer-at')
  })

  it('clears credentials when forced refresh fails with 401', async () => {
    const context = fixture()
    context.set({ ...context.get()!, expiresAt: 5000 })
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('unauthorized'), { statusCode: 401 }))

    expect(await context.ensure('gateway', { force: true })).toBeNull()
    expect(context.clear).toHaveBeenCalledTimes(1)
    expect(context.get()).toBeNull()
  })

  it('clears credentials on forced refresh when no refreshToken is present', async () => {
    const context = fixture()
    context.set({ ...context.get()!, refreshToken: '', expiresAt: 5000 })

    expect(await context.ensure('gateway', { force: true })).toBeNull()
    expect(context.clear).toHaveBeenCalledTimes(1)
    expect(context.refresh).not.toHaveBeenCalled()
    expect(context.get()).toBeNull()
  })

  it('supports forceRefresh convenience method on the refresher function', async () => {
    const context = fixture()
    context.set({ ...context.get()!, expiresAt: 5000 })

    const refreshed = await context.ensure.forceRefresh!('gateway')
    expect(refreshed).toBe('new-at')
    expect(context.refresh).toHaveBeenCalledTimes(1)
  })

  it('reuses currently stored bearer without refreshing if storage already has a different token than rejectedBearer', async () => {
    const context = fixture()
    context.set({
      accessToken: 'new-at-1',
      refreshToken: 'stable-rt',
      expiresAt: 5000,
      provider: 'self-hosted',
      userId: 'user'
    })

    const result = await context.ensure('gateway', { force: true, rejectedBearer: 'old-at' })
    expect(result).toBe('new-at-1')
    expect(context.refresh).not.toHaveBeenCalled()
    expect(context.store).not.toHaveBeenCalled()
  })

  it('does not rotate or clear newer session when a stale 401 arrives for an older rejectedBearer', async () => {
    const context = fixture()
    context.set({
      accessToken: 'brand-new-login-at',
      refreshToken: 'brand-new-login-rt',
      expiresAt: 5000,
      provider: 'self-hosted',
      userId: 'new-user'
    })

    const result = await context.ensure('gateway', { force: true, rejectedBearer: 'stale-pre-login-at' })
    expect(result).toBe('brand-new-login-at')
    expect(context.refresh).not.toHaveBeenCalled()
    expect(context.clear).not.toHaveBeenCalled()
    expect(context.get()?.accessToken).toBe('brand-new-login-at')
  })

  it('handles staggered 401s for the same rejected bearer with exactly one refresh and identical rotated bearer', async () => {
    const context = fixture()
    let serial = 0
    context.refresh.mockImplementation(async () => {
      serial++

      return {
        access_token: `rotated-at-${serial}`,
        refresh_token: 'stable-rt',
        expires_at: 5000,
        provider: 'self-hosted',
        user_id: 'user'
      }
    })

    const firstRefreshPromise = context.ensure('gateway', { force: true, rejectedBearer: 'old-at' })
    const firstResult = await firstRefreshPromise
    expect(firstResult).toBe('rotated-at-1')
    expect(context.refresh).toHaveBeenCalledTimes(1)

    const secondRefreshPromise = context.ensure('gateway', { force: true, rejectedBearer: 'old-at' })
    const secondResult = await secondRefreshPromise

    expect(secondResult).toBe('rotated-at-1')
    expect(context.refresh).toHaveBeenCalledTimes(1)
    expect(context.get()?.accessToken).toBe('rotated-at-1')
  })

  it('marks normalized base URL as terminal signed-out on authoritative 401', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('unauthorized'), { statusCode: 401 }))

    expect(context.ensure.isTerminalSignedOut!('https://gateway.example/')).toBe(false)
    await context.ensure('https://gateway.example')

    expect(context.ensure.isTerminalSignedOut!('https://gateway.example/')).toBe(true)
    expect(context.ensure.isTerminalSignedOut!('https://gateway.example')).toBe(true)
    expect(context.clear).toHaveBeenCalledTimes(1)
  })

  it('suppresses network refresh calls when base URL is in terminal signed-out state', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('unauthorized'), { statusCode: 401 }))

    await context.ensure('gateway')
    expect(context.refresh).toHaveBeenCalledTimes(1)
    expect(context.ensure.isTerminalSignedOut!('gateway')).toBe(true)

    // Subsequent calls return null immediately without network calls
    const subsequent = await context.ensure('gateway')
    expect(subsequent).toBeNull()
    expect(context.refresh).toHaveBeenCalledTimes(1)
  })

  it('isolates terminal signed-out state per normalized base URL', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('unauthorized'), { statusCode: 401 }))

    await context.ensure('https://gateway-a.example')
    expect(context.ensure.isTerminalSignedOut!('https://gateway-a.example')).toBe(true)
    expect(context.ensure.isTerminalSignedOut!('https://gateway-b.example')).toBe(false)

    // Gateway B can still refresh
    context.set({
      accessToken: 'at-b',
      refreshToken: 'rt-b',
      expiresAt: 1000,
      provider: 'self-hosted',
      userId: 'user'
    })
    const resultB = await context.ensure('https://gateway-b.example')
    expect(resultB).toBe('new-at')
    expect(context.refresh).toHaveBeenCalledTimes(2)
  })

  it('does not mark base URL as signed-out when a stale in-flight 401 arrives after newer login', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('stale expired'), { statusCode: 401 }))

    const pending = context.ensure('gateway')
    // Fresh login occurs before rejection resolves
    context.set({
      accessToken: 'brand-new-token',
      refreshToken: 'brand-new-rt',
      expiresAt: 5000,
      provider: 'self-hosted',
      userId: 'user'
    })

    const result = await pending
    expect(result).toBe('brand-new-token')
    expect(context.clear).not.toHaveBeenCalled()
    expect(context.ensure.isTerminalSignedOut!('gateway')).toBe(false)
  })

  it('clears terminal signed-out state when clearTerminalSignedOut is called on confirmed sign-in', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('unauthorized'), { statusCode: 401 }))

    await context.ensure('gateway')
    expect(context.ensure.isTerminalSignedOut!('gateway')).toBe(true)

    context.ensure.clearTerminalSignedOut!('gateway')
    expect(context.ensure.isTerminalSignedOut!('gateway')).toBe(false)
  })

  it('invokes io.markSignedOut on authoritative 401 and io.clearSignedOut on clearTerminalSignedOut', async () => {
    const context = fixture()
    context.refresh.mockRejectedValueOnce(Object.assign(new Error('unauthorized'), { statusCode: 401 }))

    await context.ensure('https://gateway.example')
    expect(context.markSignedOut).toHaveBeenCalledWith('https://gateway.example', 'authoritative_401')

    context.ensure.clearTerminalSignedOut!('https://gateway.example')
    expect(context.clearSignedOut).toHaveBeenCalledWith('https://gateway.example')
  })

  it('marks terminal signed-out on explicit logout and suppresses subsequent refresh calls', async () => {
    const context = fixture()
    context.ensure.markTerminalSignedOut!('https://gateway.example', 'explicit_logout')

    expect(context.ensure.isTerminalSignedOut!('https://gateway.example')).toBe(true)
    expect(context.markSignedOut).toHaveBeenCalledWith('https://gateway.example', 'explicit_logout')

    const result = await context.ensure('https://gateway.example')
    expect(result).toBeNull()
    expect(context.refresh).not.toHaveBeenCalled()
  })
})
