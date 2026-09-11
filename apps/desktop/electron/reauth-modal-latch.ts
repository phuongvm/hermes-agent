/**
 * reauth-modal-latch.ts
 *
 * Process-wide singleton latch preventing multiple concurrent reauth modals
 * across pooled remote backend connections (OpenSpec desktop-reconnect-session-resilience Group 6).
 *
 * When multiple pooled connections (e.g., conn:host::profile-a and conn:host::profile-b)
 * encounter auth failures (isReauthRequired) simultaneously:
 * 1. The first connection acquires the latch and opens the re-auth modal.
 * 2. Subsequent connections are queued to wait for the outcome of the active modal.
 * 3. On successful re-auth, the auth state is resolved for all queued/waiting connections.
 * 4. On failure/cancellation, the error/unauthenticated state is surfaced once without duplicate modals.
 */

export interface ConnectionAuthState {
  connectionKey: string
  status: 'authenticated' | 'unauthenticated' | 'reauth_required'
  lastResolvedAt?: number
  error?: string | null
}

export interface ReauthOutcome {
  ok?: boolean
  connected?: boolean
  error?: string | null
  [key: string]: any
}

export type AuthStateResolver = (
  connectionKey: string,
  outcome: any
) => Promise<void> | void

export class ReauthModalLatch {
  #activeModal: {
    connectionKey: string
    promise: Promise<any>
    openedAt: number
  } | null = null

  #waitingQueue: Array<{
    connectionKey: string
    resolve: (value: any) => void
    reject: (reason?: unknown) => void
    queuedAt: number
  }> = []

  #authStates = new Map<string, ConnectionAuthState>()
  #authStateResolver?: AuthStateResolver

  /**
   * Whether a re-auth modal is currently active/displayed.
   */
  isModalActive(): boolean {
    return this.#activeModal !== null
  }

  /**
   * The connection key that initiated the currently active modal, or null.
   */
  getActiveConnection(): string | null {
    return this.#activeModal?.connectionKey ?? null
  }

  /**
   * Number of connections currently queued waiting for the active modal.
   */
  getWaitingCount(): number {
    return this.#waitingQueue.length
  }

  /**
   * List of connection keys currently queued waiting for the active modal.
   */
  getQueuedConnections(): string[] {
    return this.#waitingQueue.map(item => item.connectionKey)
  }

  /**
   * Check if a specific connection is in the waiting queue.
   */
  isConnectionQueued(connectionKey: string): boolean {
    return this.#waitingQueue.some(item => item.connectionKey === connectionKey)
  }

  /**
   * Get tracked auth state for a connection.
   */
  getAuthState(connectionKey: string): ConnectionAuthState | undefined {
    const state = this.#authStates.get(connectionKey)
    return state ? { ...state } : undefined
  }

  /**
   * Set or update tracked auth state for a connection.
   */
  setAuthState(connectionKey: string, state: Partial<ConnectionAuthState>): ConnectionAuthState {
    const existing = this.#authStates.get(connectionKey) || {
      connectionKey,
      status: 'unauthenticated'
    }
    const updated: ConnectionAuthState = {
      ...existing,
      ...state,
      connectionKey
    }
    this.#authStates.set(connectionKey, updated)
    return { ...updated }
  }

  /**
   * Register a custom resolver callback to run on successful re-auth for each queued connection.
   */
  setAuthStateResolver(resolver?: AuthStateResolver): void {
    this.#authStateResolver = resolver
  }

  /**
   * Request re-authentication for a connection.
   * If no modal is active, runs openModal() and marks modal active.
   * If a modal is already active, queues connectionKey to wait for the active modal's outcome.
   */
  async triggerReauth<T = any>(
    connectionKey: string,
    openModal: () => Promise<T>
  ): Promise<T> {
    this.setAuthState(connectionKey, { status: 'reauth_required' })

    // If modal is already active, queue this connection to wait for the active modal's outcome
    if (this.#activeModal) {
      return new Promise<T>((resolve, reject) => {
        this.#waitingQueue.push({
          connectionKey,
          resolve,
          reject,
          queuedAt: Date.now()
        })
      })
    }

    // Modal is not active: acquire the latch
    let modalPromise: Promise<T>
    try {
      modalPromise = openModal()
    } catch (err) {
      modalPromise = Promise.reject(err)
    }

    this.#activeModal = {
      connectionKey,
      promise: modalPromise as Promise<ReauthOutcome>,
      openedAt: Date.now()
    }

    try {
      const outcome: any = await modalPromise
      const isSuccess = outcome?.ok !== false && outcome?.connected !== false

      if (isSuccess) {
        // Resolve auth state for the active connection
        const now = Date.now()
        this.setAuthState(connectionKey, {
          status: 'authenticated',
          lastResolvedAt: now,
          error: null
        })

        // Resolve auth state for all queued/waiting connections
        const waiting = [...this.#waitingQueue]
        this.#waitingQueue = []

        for (const item of waiting) {
          this.setAuthState(item.connectionKey, {
            status: 'authenticated',
            lastResolvedAt: now,
            error: null
          })

          if (this.#authStateResolver) {
            try {
              await this.#authStateResolver(item.connectionKey, outcome)
            } catch {
              // resolver error should not block resolving the connection
            }
          }

          item.resolve(outcome)
        }
      } else {
        // Reauth modal closed without successful connection (e.g. cancelled)
        this.setAuthState(connectionKey, {
          status: 'unauthenticated',
          error: outcome?.error ?? 'Re-authentication not completed'
        })

        const waiting = [...this.#waitingQueue]
        this.#waitingQueue = []

        for (const item of waiting) {
          this.setAuthState(item.connectionKey, {
            status: 'unauthenticated',
            error: outcome?.error ?? 'Re-authentication not completed'
          })
          item.resolve(outcome)
        }
      }

      return outcome
    } catch (error) {
      // Reauth modal failed with an error
      const errorMessage = error instanceof Error ? error.message : String(error)
      this.setAuthState(connectionKey, {
        status: 'unauthenticated',
        error: errorMessage
      })

      const waiting = [...this.#waitingQueue]
      this.#waitingQueue = []

      for (const item of waiting) {
        this.setAuthState(item.connectionKey, {
          status: 'unauthenticated',
          error: errorMessage
        })
        item.reject(error)
      }

      throw error
    } finally {
      this.#activeModal = null
    }
  }

  /**
   * Alias for triggerReauth to explicitly express handling an isReauthRequired trigger.
   */
  handleReauthRequired<T = any>(
    connectionKey: string,
    openModal: () => Promise<T>
  ): Promise<T> {
    return this.triggerReauth(connectionKey, openModal)
  }

  /**
   * Reset the latch and queues (primarily for test teardown).
   */
  reset(): void {
    this.#activeModal = null
    this.#waitingQueue = []
    this.#authStates.clear()
    this.#authStateResolver = undefined
  }
}

export const reauthModalLatch = new ReauthModalLatch()

export function isReauthModalActive(): boolean {
  return reauthModalLatch.isModalActive()
}

export function resetReauthModalLatch(): void {
  reauthModalLatch.reset()
}

export function getQueuedReauthConnections(): string[] {
  return reauthModalLatch.getQueuedConnections()
}

export function triggerReauth<T = any>(
  connectionKey: string,
  openModal: () => Promise<T>
): Promise<T> {
  return reauthModalLatch.triggerReauth(connectionKey, openModal)
}
