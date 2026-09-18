import { describe, expect, it, vi } from 'vitest'

import {
  DEFAULT_BASE_DELAY_MS,
  DEFAULT_CAP_MS,
  MAX_RECONNECT_ATTEMPTS,
  reconnectBackoffDelayMs,
  ReconnectBackoffTracker,
} from './reconnect-backoff.js'

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

  it('resets to the attempt-0 ceiling after a successful connection (caller passes attempt back to 0)', () => {
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(1)

    try {
      reconnectBackoffDelayMs(0, { baseDelayMs: 300 })
      reconnectBackoffDelayMs(1, { baseDelayMs: 300 })
      const afterSeveralFailures = reconnectBackoffDelayMs(2, { baseDelayMs: 300 })
      const afterReset = reconnectBackoffDelayMs(0, { baseDelayMs: 300 })

      expect(afterSeveralFailures).toBe(1200)
      expect(afterReset).toBe(300)
    } finally {
      randomSpy.mockRestore()
    }
  })

  it('treats negative attempt numbers as attempt 0 rather than throwing or returning a negative delay', () => {
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(1)

    try {
      expect(reconnectBackoffDelayMs(-5, { baseDelayMs: 300 })).toBe(300)
    } finally {
      randomSpy.mockRestore()
    }
  })

  it('uses sane defaults when no options are passed', () => {
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(1)

    try {
      expect(reconnectBackoffDelayMs(0)).toBe(DEFAULT_BASE_DELAY_MS)
      expect(reconnectBackoffDelayMs(100)).toBe(DEFAULT_CAP_MS)
    } finally {
      randomSpy.mockRestore()
    }
  })

  it('jitter: false returns the exact ceiling — the ladder web prints in its banner', () => {
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.1)

    try {
      expect([0, 1, 2, 3].map(a => reconnectBackoffDelayMs(a, { baseDelayMs: 1000, capMs: 30_000, jitter: false }))).toEqual(
        [1000, 2000, 4000, 8000]
      )
      expect(reconnectBackoffDelayMs(99, { baseDelayMs: 1000, capMs: 30_000, jitter: false })).toBe(30_000)
      expect(reconnectBackoffDelayMs(10_000, { jitter: false })).toBeLessThanOrEqual(DEFAULT_CAP_MS)
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
