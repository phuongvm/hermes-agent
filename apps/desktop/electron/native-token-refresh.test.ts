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
})
