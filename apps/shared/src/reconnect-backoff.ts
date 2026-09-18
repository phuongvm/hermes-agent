/**
 * Equal-jitter capped exponential backoff for gateway WebSocket reconnects.
 *
 * Implements capped exponential backoff with equal jitter:
 *   ceiling = min(30000, 1000 * 2 ** min(n, 15))
 *   delay = ceiling / 2 + U[0, ceiling / 2)
 *
 * This guarantees a positive lower bound (500–1000ms on attempt 0, 15–30s at saturation),
 * preventing immediate retry bursts during fleet outages while desynchronizing client dials.
 *
 * Respects server `Retry-After` headers, enforces streak reset only after 30s continuously
 * open with successful ping, and transitions to exhausted after 12 retries or 5 minutes.
 *
 * `jitter: false` returns the ceiling itself — the deterministic ladder the
 * web dashboard renders into its "reconnecting in Ns" banner.
 */

export interface ReconnectBackoffOptions {
  /** Ceiling on the exponential delay before jitter is applied, in ms (default: 30,000). */
  capMs?: number
  /** Delay for the first retry (attempt 0) before jitter is applied, in ms (default: 1,000). */
  baseDelayMs?: number
  /** Optional server-specified Retry-After delay in ms. */
  retryAfterMs?: number
  /** Equal jitter (default) or the bare ceiling. */
  jitter?: boolean
}

export const DEFAULT_BASE_DELAY_MS = 1_000
export const DEFAULT_CAP_MS = 30_000
export const MAX_RECONNECT_ATTEMPTS = 12
export const MAX_RECONNECT_DURATION_MS = 5 * 60 * 1_000 // 5 minutes
export const STREAK_RESET_WINDOW_MS = 30_000 // 30 seconds

/**
 * Returns delay in milliseconds for reconnect attempt `attempt` (0-indexed).
 * delay = ceiling / 2 + random * (ceiling / 2)
 */
export function reconnectBackoffDelayMs(attempt: number, options: ReconnectBackoffOptions = {}): number {
  const baseDelayMs = options.baseDelayMs ?? DEFAULT_BASE_DELAY_MS
  const capMs = options.capMs ?? DEFAULT_CAP_MS
  const safeAttempt = Math.min(Math.max(0, Math.trunc(attempt)), 15)

  const ceiling = Math.min(capMs, baseDelayMs * 2 ** safeAttempt)

  if (options.jitter === false) {
    if (typeof options.retryAfterMs === 'number' && Number.isFinite(options.retryAfterMs) && options.retryAfterMs > 0) {
      return Math.max(ceiling, options.retryAfterMs)
    }
    return ceiling
  }

  const halfCeiling = ceiling / 2
  const jittered = halfCeiling + Math.random() * halfCeiling

  if (typeof options.retryAfterMs === 'number' && Number.isFinite(options.retryAfterMs) && options.retryAfterMs > 0) {
    return Math.max(jittered, options.retryAfterMs)
  }

  return jittered
}

/**
 * State machine for tracking reconnect backoff streak and exhaustion.
 */
export class ReconnectBackoffTracker {
  private attempt = 0
  private firstFailureTime: number | null = null
  private openTime: number | null = null
  private hasPinged = false

  constructor(
    private readonly maxAttempts = MAX_RECONNECT_ATTEMPTS,
    private readonly maxDurationMs = MAX_RECONNECT_DURATION_MS,
    private readonly streakResetMs = STREAK_RESET_WINDOW_MS,
  ) {}

  get currentAttempt(): number {
    return this.attempt
  }

  recordFailure(now = Date.now()): { delayMs: number; exhausted: boolean } {
    this.openTime = null
    this.hasPinged = false

    if (this.firstFailureTime === null) {
      this.firstFailureTime = now
    }

    const elapsed = now - this.firstFailureTime
    if (this.attempt >= this.maxAttempts || elapsed >= this.maxDurationMs) {
      return { delayMs: 0, exhausted: true }
    }

    const delayMs = reconnectBackoffDelayMs(this.attempt)
    this.attempt += 1

    return { delayMs, exhausted: false }
  }

  recordConnected(now = Date.now()): void {
    this.openTime = now
    this.hasPinged = false
  }

  recordPingResponse(now = Date.now()): void {
    this.hasPinged = true
    this.checkStreakReset(now)
  }

  checkStreakReset(now = Date.now()): boolean {
    if (this.openTime !== null && this.hasPinged && now - this.openTime >= this.streakResetMs) {
      this.reset()
      return true
    }
    return false
  }

  reset(): void {
    this.attempt = 0
    this.firstFailureTime = null
    this.openTime = null
    this.hasPinged = false
  }
}
