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

  const ensure = createNativeTokenRefresher({ load: () => tokens, store, clear, refresh, now: () => now })

  return {
    ensure,
    refresh,
    clear,
    store,
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
})
