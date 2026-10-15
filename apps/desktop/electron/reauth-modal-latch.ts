/**
 * reauth-modal-latch.ts
 *
 * Process-wide singleton latch preventing multiple concurrent reauth modals
 * across pooled remote backend connections (OpenSpec desktop-reconnect-session-resilience Group 6).
 *
 * Separates process-wide UI concurrency (at most one modal displayed at a time)
 * from per-origin authentication identity:
 * 1. Compatible / same-origin connections coalesce onto a single modal.
 * 2. Independent origins serialize their modal displays without cross-contaminating credentials.
 * 3. Atomic draining ensures late arrivals and async resolvers never strand waiters.
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

export interface ActiveModalState {
  connectionKey: string
  origin: string
  promise: Promise<any>
  openedAt: number
}

export interface WaitingQueueItem<T = any> {
  connectionKey: string
  origin: string
  openModal: () => Promise<T>
  resolve: (value: any) => void
  reject: (reason?: unknown) => void
  queuedAt: number
}

export function extractAuthOrigin(connectionKey: string): string {
  try {
    if (connectionKey.startsWith('http://') || connectionKey.startsWith('https://')) {
      return new URL(connectionKey).origin.toLowerCase()
    }
  } catch {
    // fall through
  }

  if (connectionKey.includes('::')) {
    return connectionKey.split('::')[0].trim().toLowerCase()
  }

  return connectionKey.trim().toLowerCase()
}

export class ReauthModalLatch {
  #activeModal: ActiveModalState | null = null
  #waitingQueue: WaitingQueueItem[] = []
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
   * If a modal is already active:
   *  - If same origin: coalesces onto the active modal's outcome.
   *  - If different origin: queues for serialized execution after active modal finishes.
   */
  triggerReauth<T = any>(
    connectionKey: string,
    openModal: () => Promise<T>
  ): Promise<T> {
    this.setAuthState(connectionKey, { status: 'reauth_required' })
    const origin = extractAuthOrigin(connectionKey)

    if (this.#activeModal) {
      return new Promise<T>((resolve, reject) => {
        this.#waitingQueue.push({
          connectionKey,
          origin,
          openModal,
          resolve,
          reject,
          queuedAt: Date.now()
        })
      })
    }

    return this.#executeModal(connectionKey, origin, openModal)
  }

  async #executeModal<T = any>(
    connectionKey: string,
    origin: string,
    openModal: () => Promise<T>
  ): Promise<T> {
    let modalPromise: Promise<T>

    try {
      modalPromise = openModal()
    } catch (err) {
      modalPromise = Promise.reject(err)
    }

    this.#activeModal = {
      connectionKey,
      origin,
      promise: modalPromise as Promise<ReauthOutcome>,
      openedAt: Date.now()
    }

    try {
      const outcome: any = await modalPromise
      const isSuccess = outcome?.ok !== false && outcome?.connected !== false

      if (isSuccess) {
        const now = Date.now()
        this.setAuthState(connectionKey, {
          status: 'authenticated',
          lastResolvedAt: now,
          error: null
        })

        // Drain compatible waiters sharing the same origin
        const compatibleWaiters: WaitingQueueItem[] = []
        const remainingQueue: WaitingQueueItem[] = []

        for (const item of this.#waitingQueue) {
          if (item.origin === origin) {
            compatibleWaiters.push(item)
          } else {
            remainingQueue.push(item)
          }
        }

        this.#waitingQueue = remainingQueue

        for (const item of compatibleWaiters) {
          if (this.#authStateResolver) {
            try {
              await this.#authStateResolver(item.connectionKey, outcome)
              this.setAuthState(item.connectionKey, {
                status: 'authenticated',
                lastResolvedAt: now,
                error: null
              })
              item.resolve(outcome)
            } catch (resolverError) {
              const msg = resolverError instanceof Error ? resolverError.message : String(resolverError)
              this.setAuthState(item.connectionKey, {
                status: 'unauthenticated',
                error: msg
              })
              item.reject(resolverError)
            }
          } else {
            this.setAuthState(item.connectionKey, {
              status: 'authenticated',
              lastResolvedAt: now,
              error: null
            })
            item.resolve(outcome)
          }
        }
      } else {
        const errorMsg = outcome?.error ?? 'Re-authentication not completed'
        this.setAuthState(connectionKey, {
          status: 'unauthenticated',
          error: errorMsg
        })

        const compatibleWaiters: WaitingQueueItem[] = []
        const remainingQueue: WaitingQueueItem[] = []

        for (const item of this.#waitingQueue) {
          if (item.origin === origin) {
            compatibleWaiters.push(item)
          } else {
            remainingQueue.push(item)
          }
        }

        this.#waitingQueue = remainingQueue

        for (const item of compatibleWaiters) {
          this.setAuthState(item.connectionKey, {
            status: 'unauthenticated',
            error: errorMsg
          })
          item.resolve(outcome)
        }
      }

      return outcome
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error)
      this.setAuthState(connectionKey, {
        status: 'unauthenticated',
        error: errorMessage
      })

      const compatibleWaiters: WaitingQueueItem[] = []
      const remainingQueue: WaitingQueueItem[] = []

      for (const item of this.#waitingQueue) {
        if (item.origin === origin) {
          compatibleWaiters.push(item)
        } else {
          remainingQueue.push(item)
        }
      }

      this.#waitingQueue = remainingQueue

      for (const item of compatibleWaiters) {
        this.setAuthState(item.connectionKey, {
          status: 'unauthenticated',
          error: errorMessage
        })
        item.reject(error)
      }

      throw error
    } finally {
      if (this.#waitingQueue.length > 0) {
        const next = this.#waitingQueue.shift()!
        void this.#executeModal(next.connectionKey, next.origin, next.openModal).then(
          next.resolve,
          next.reject
        )
      } else {
        this.#activeModal = null
      }
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
    const queue = [...this.#waitingQueue]
    this.#waitingQueue = []

    for (const item of queue) {
      item.reject(new Error('Reauth latch reset'))
    }

    this.#activeModal = null
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
