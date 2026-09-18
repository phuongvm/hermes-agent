import { afterEach, describe, expect, it, vi } from 'vitest'

import { buildReconnectOwnerKey, reconnectGateway, registerGatewayReconnect } from './gateway-reconnect'
import { resetAuthTerminalState, setTerminalSignedOut } from './auth-terminal-state'

const disposers: Array<() => void> = []

afterEach(() => {
  resetAuthTerminalState()
  while (disposers.length > 0) {
    disposers.pop()?.()
  }
})

describe('gateway reconnect controller', () => {
  it('coalesces repeated requests onto one active reconnect', async () => {
    let finish: (() => void) | undefined

    const handler = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<void>(resolve => {
            finish = resolve
          })
      )
      .mockResolvedValueOnce(undefined)

    disposers.push(registerGatewayReconnect(handler))

    const first = reconnectGateway()
    const second = reconnectGateway()

    expect(second).toBe(first)
    await Promise.resolve()
    expect(handler).toHaveBeenCalledTimes(1)

    finish?.()
    await first

    await reconnectGateway()
    expect(handler).toHaveBeenCalledTimes(2)
  })

  it('only lets the current registration remove itself', async () => {
    const stale = vi.fn()
    const current = vi.fn()
    const disposeStale = registerGatewayReconnect(stale)
    disposers.push(registerGatewayReconnect(current))

    disposeStale()
    await reconnectGateway()

    expect(stale).not.toHaveBeenCalled()
    expect(current).toHaveBeenCalledOnce()
  })

  it('rejects when the gateway boot owner is not mounted', async () => {
    await expect(reconnectGateway()).rejects.toThrow('Gateway reconnect is unavailable')
  })

  it('suppresses reconnect when the endpoint is marked terminal signed-out', async () => {
    const endpoint = 'https://gateway.example.com'
    setTerminalSignedOut(endpoint, true, 'test_terminal')

    const handler = vi.fn().mockResolvedValue(undefined)
    disposers.push(registerGatewayReconnect(handler, { basePath: endpoint }))

    await expect(reconnectGateway({ basePath: endpoint })).rejects.toMatchObject({
      code: 'ERR_SIGNED_OUT'
    })
    expect(handler).not.toHaveBeenCalled()
  })
})

describe('gateway-reconnect single-flight owner', () => {
  it('builds canonical owner key normalizing endpoint', () => {
    const key1 = buildReconnectOwnerKey({
      connectionId: 'c1',
      basePath: 'http://localhost:8080/',
      profile: 'p1',
      windowId: 'w1',
      logicalOwner: 'owner1',
    })
    const key2 = buildReconnectOwnerKey({
      connectionId: 'c1',
      basePath: 'http://localhost:8080',
      profile: 'p1',
      windowId: 'w1',
      logicalOwner: 'owner1',
    })
    expect(key1).toBe(key2)
  })

  it('coalesces concurrent reconnect calls into a single in-flight promise', async () => {
    let callCount = 0
    const unregister = registerGatewayReconnect(async () => {
      callCount++
      await new Promise(r => setTimeout(r, 20))
    }, { connectionId: 'test-conn' })

    try {
      const [p1, p2, p3] = [
        reconnectGateway({ connectionId: 'test-conn' }),
        reconnectGateway({ connectionId: 'test-conn' }),
        reconnectGateway({ connectionId: 'test-conn' }),
      ]
      await Promise.all([p1, p2, p3])
      expect(callCount).toBe(1)
    } finally {
      unregister()
    }
  })
})
