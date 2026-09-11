import assert from 'node:assert/strict'

import { beforeEach, describe, it, vi } from 'vitest'

import {
  getQueuedReauthConnections,
  isReauthModalActive,
  ReauthModalLatch,
  reauthModalLatch,
  resetReauthModalLatch,
  triggerReauth
} from './reauth-modal-latch'

describe('ReauthModalLatch (Singleton Process-Wide Latch)', () => {
  let latch: ReauthModalLatch

  beforeEach(() => {
    resetReauthModalLatch()
    latch = new ReauthModalLatch()
  })

  it('initially has no active modal and empty queue', () => {
    assert.equal(latch.isModalActive(), false)
    assert.equal(latch.getActiveConnection(), null)
    assert.equal(latch.getWaitingCount(), 0)
    assert.deepEqual(latch.getQueuedConnections(), [])
  })

  it('two simultaneous reauth triggers → exactly one modal opened', async () => {
    let modalCallCount = 0
    let resolveModal: (val: any) => void

    const modalPromise = new Promise(resolve => {
      resolveModal = resolve
    })

    const openModal = vi.fn().mockImplementation(() => {
      modalCallCount += 1
      return modalPromise
    })

    const connA = 'conn:host::profile-a'
    const connB = 'conn:host::profile-b'

    // Trigger both concurrently
    const p1 = latch.triggerReauth(connA, openModal)
    const p2 = latch.triggerReauth(connB, openModal)

    // Modal is now active
    assert.equal(latch.isModalActive(), true)
    assert.equal(latch.getActiveConnection(), connA)
    assert.equal(modalCallCount, 1)
    assert.equal(latch.getWaitingCount(), 1)
    assert.deepEqual(latch.getQueuedConnections(), [connB])

    // Resolve the active modal
    resolveModal!({ ok: true, connected: true, token: 'fresh-token' })

    const [res1, res2] = await Promise.all([p1, p2])

    // Both received the same resolution
    assert.deepEqual(res1, { ok: true, connected: true, token: 'fresh-token' })
    assert.deepEqual(res2, { ok: true, connected: true, token: 'fresh-token' })

    // OpenModal was called only ONCE
    assert.equal(modalCallCount, 1)
    assert.equal(latch.isModalActive(), false)
    assert.equal(latch.getWaitingCount(), 0)
  })

  it('successful reauth resolves auth state for all queued/waiting connections', async () => {
    let resolveModal: (val: any) => void
    const openModal = vi.fn().mockImplementation(
      () =>
        new Promise(resolve => {
          resolveModal = resolve
        })
    )

    const connA = 'conn:host::profile-a'
    const connB = 'conn:host::profile-b'
    const connC = 'conn:host::profile-c'

    const p1 = latch.triggerReauth(connA, openModal)
    const p2 = latch.triggerReauth(connB, openModal)
    const p3 = latch.triggerReauth(connC, openModal)

    assert.equal(latch.getWaitingCount(), 2)
    assert.deepEqual(latch.getQueuedConnections(), [connB, connC])

    assert.equal(latch.getAuthState(connA)?.status, 'reauth_required')
    assert.equal(latch.getAuthState(connB)?.status, 'reauth_required')
    assert.equal(latch.getAuthState(connC)?.status, 'reauth_required')

    resolveModal!({ ok: true, connected: true })

    await Promise.all([p1, p2, p3])

    // All connections now have authenticated status
    assert.equal(latch.getAuthState(connA)?.status, 'authenticated')
    assert.ok(latch.getAuthState(connA)?.lastResolvedAt)
    assert.equal(latch.getAuthState(connB)?.status, 'authenticated')
    assert.ok(latch.getAuthState(connB)?.lastResolvedAt)
    assert.equal(latch.getAuthState(connC)?.status, 'authenticated')
    assert.ok(latch.getAuthState(connC)?.lastResolvedAt)
  })

  it('custom auth state resolver is called for all queued connections on success', async () => {
    const resolverCalls: string[] = []
    latch.setAuthStateResolver(async connectionKey => {
      resolverCalls.push(connectionKey)
    })

    const p1 = latch.triggerReauth('conn:remote::alpha', async () => ({ ok: true, connected: true }))
    const p2 = latch.triggerReauth('conn:remote::beta', async () => ({ ok: true, connected: true }))
    const p3 = latch.triggerReauth('conn:remote::gamma', async () => ({ ok: true, connected: true }))

    await Promise.all([p1, p2, p3])

    assert.deepEqual(resolverCalls, ['conn:remote::beta', 'conn:remote::gamma'])
  })

  it('failed reauth surfaces error once and rejects all waiting connections', async () => {
    let rejectModal: (err: any) => void
    let modalCallCount = 0

    const openModal = vi.fn().mockImplementation(() => {
      modalCallCount += 1
      return new Promise((_resolve, reject) => {
        rejectModal = reject
      })
    })

    const connA = 'conn:host::profile-a'
    const connB = 'conn:host::profile-b'

    const p1 = latch.triggerReauth(connA, openModal)
    const p2 = latch.triggerReauth(connB, openModal)

    rejectModal!(new Error('User closed login window'))

    await assert.rejects(p1, { message: 'User closed login window' })
    await assert.rejects(p2, { message: 'User closed login window' })

    // Modal was only called once
    assert.equal(modalCallCount, 1)
    // Latch is released
    assert.equal(latch.isModalActive(), false)
    assert.equal(latch.getWaitingCount(), 0)

    // Status updated to unauthenticated with error
    assert.equal(latch.getAuthState(connA)?.status, 'unauthenticated')
    assert.equal(latch.getAuthState(connA)?.error, 'User closed login window')
    assert.equal(latch.getAuthState(connB)?.status, 'unauthenticated')
    assert.equal(latch.getAuthState(connB)?.error, 'User closed login window')
  })

  it('unsuccessful reauth outcome (cancelled) resolves with non-connected state for all callers', async () => {
    const openModal = vi.fn().mockResolvedValue({ ok: true, connected: false })

    const p1 = latch.triggerReauth('conn:x::1', openModal)
    const p2 = latch.triggerReauth('conn:x::2', openModal)

    const [r1, r2] = await Promise.all([p1, p2])

    assert.deepEqual(r1, { ok: true, connected: false })
    assert.deepEqual(r2, { ok: true, connected: false })
    assert.equal(latch.getAuthState('conn:x::1')?.status, 'unauthenticated')
    assert.equal(latch.getAuthState('conn:x::2')?.status, 'unauthenticated')
  })

  it('releases latch allowing subsequent reauth modal after previous settles', async () => {
    let modalCallCount = 0

    const openModal1 = vi.fn().mockImplementation(async () => {
      modalCallCount += 1
      return { ok: true, connected: true }
    })
    const openModal2 = vi.fn().mockImplementation(async () => {
      modalCallCount += 1
      return { ok: true, connected: true }
    })

    await latch.triggerReauth('conn:1', openModal1)
    assert.equal(modalCallCount, 1)
    assert.equal(latch.isModalActive(), false)

    // Subsequent reauth
    await latch.triggerReauth('conn:2', openModal2)
    assert.equal(modalCallCount, 2)
    assert.equal(latch.isModalActive(), false)
  })

  it('works with the default singleton export and helper functions', async () => {
    assert.equal(isReauthModalActive(), false)

    let resolveModal: (val: any) => void
    const p1 = triggerReauth(
      'conn:default::a',
      () =>
        new Promise(r => {
          resolveModal = r
        })
    )
    const p2 = triggerReauth('conn:default::b', async () => ({ ok: true }))

    assert.equal(isReauthModalActive(), true)
    assert.deepEqual(getQueuedReauthConnections(), ['conn:default::b'])

    resolveModal!({ ok: true, connected: true })
    await Promise.all([p1, p2])

    assert.equal(isReauthModalActive(), false)
    assert.deepEqual(getQueuedReauthConnections(), [])
  })

  it('handles synchronous throw in openModal cleanly', async () => {
    const throwingModal = () => {
      throw new Error('Sync modal crash')
    }

    await assert.rejects(latch.triggerReauth('conn:crash', throwingModal), {
      message: 'Sync modal crash'
    })

    assert.equal(latch.isModalActive(), false)
  })
})
