import { describe, expect, it, vi } from 'vitest'

import {
  DEFAULT_BASE_DELAY_MS,
  DEFAULT_CAP_MS,
  MAX_RECONNECT_ATTEMPTS,
  reconnectBackoffDelayMs,
  ReconnectBackoffTracker,
} from './reconnect-backoff'
import {
  buildReconnectOwnerKey,
  reconnectGateway,
  registerGatewayReconnect,
} from '../store/gateway-reconnect'

describe('reconnectBackoffDelayMs', () => {
  it('enforces equal jitter with a positive floor (500–1000ms on attempt 0)', () => {
    const randomSpy = vi.spyOn(Math, 'random')

    try {
      // ceiling = 1000, half = 500
      randomSpy.mockReturnValue(0)
      expect(reconnectBackoffDelayMs(0)).toBe(500)

      randomSpy.mockReturnValue(0.5)
      expect(reconnectBackoffDelayMs(0)).toBe(750)

      randomSpy.mockReturnValue(1)
      expect(reconnectBackoffDelayMs(0)).toBe(1000)
    } finally {
      randomSpy.mockRestore()
    }
  })

  it('increases the delay ceiling across consecutive failed attempts with equal jitter', () => {
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(1)

    try {
      const delays = [0, 1, 2, 3, 4, 5].map(attempt => reconnectBackoffDelayMs(attempt))
      expect(delays).toEqual([1000, 2000, 4000, 8000, 16000, 30000])
    } finally {
      randomSpy.mockRestore()
    }
  })

  it('caps delay at 30,000ms and guarantees 15,000–30,000ms at saturation', () => {
    const randomSpy = vi.spyOn(Math, 'random')

    try {
      randomSpy.mockReturnValue(0)
      expect(reconnectBackoffDelayMs(10)).toBe(15000)

      randomSpy.mockReturnValue(1)
      expect(reconnectBackoffDelayMs(10)).toBe(30000)
      expect(reconnectBackoffDelayMs(50)).toBe(30000)
    } finally {
      randomSpy.mockRestore()
    }
  })

  it('honors Retry-After header when it exceeds the jittered delay', () => {
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0) // delay = 500ms

    try {
      expect(reconnectBackoffDelayMs(0, { retryAfterMs: 5000 })).toBe(5000)
      expect(reconnectBackoffDelayMs(0, { retryAfterMs: 200 })).toBe(500)
    } finally {
      randomSpy.mockRestore()
    }
  })
})

describe('ReconnectBackoffTracker', () => {
  it('resets streak only after 30s open with successful ping', () => {
    const tracker = new ReconnectBackoffTracker()
    tracker.recordFailure(1000)
    expect(tracker.currentAttempt).toBe(1)

    tracker.recordConnected(2000)
    tracker.recordPingResponse(2500)
    expect(tracker.currentAttempt).toBe(1)

    expect(tracker.checkStreakReset(32500)).toBe(true)
    expect(tracker.currentAttempt).toBe(0)
  })

  it('transitions to exhausted after 12 retries or 5 minutes', () => {
    const tracker = new ReconnectBackoffTracker()
    let now = 1000
    for (let i = 0; i < MAX_RECONNECT_ATTEMPTS; i++) {
      const res = tracker.recordFailure(now)
      expect(res.exhausted).toBe(false)
      now += 1000
    }
    const res = tracker.recordFailure(now)
    expect(res.exhausted).toBe(true)
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
