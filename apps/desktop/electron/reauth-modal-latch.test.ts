import assert from 'node:assert/strict'

import { beforeEach, describe, it, vi } from 'vitest'

import {
  getQueuedReauthConnections,
  isReauthModalActive,
  ReauthModalLatch,
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

  it('serializes independent login work across different origins without cross-origin outcome leakage (C2)', async () => {
    let resolveA: (val: any) => void
    let loginBCalls = 0

    const pA = latch.triggerReauth(
      'https://a.invalid',
      () =>
        new Promise(resolve => {
          resolveA = resolve
        })
    )

    const pB = latch.triggerReauth('https://b.invalid', async () => {
      loginBCalls += 1

      return { ok: true, connected: true, baseUrl: 'https://b.invalid' }
    })

    assert.equal(latch.isModalActive(), true)
    assert.equal(latch.getActiveConnection(), 'https://a.invalid')
    assert.equal(loginBCalls, 0)
    assert.equal(latch.getWaitingCount(), 1)
    assert.deepEqual(latch.getQueuedConnections(), ['https://b.invalid'])

    resolveA!({ ok: true, connected: true, baseUrl: 'https://a.invalid' })

    const [resultA, resultB] = await Promise.all([pA, pB])

    assert.equal(loginBCalls, 1)
    assert.deepEqual(resultA, { ok: true, connected: true, baseUrl: 'https://a.invalid' })
    assert.deepEqual(resultB, { ok: true, connected: true, baseUrl: 'https://b.invalid' })
    assert.equal(latch.getAuthState('https://a.invalid')?.status, 'authenticated')
    assert.equal(latch.getAuthState('https://b.invalid')?.status, 'authenticated')
    assert.equal(latch.isModalActive(), false)
    assert.equal(latch.getWaitingCount(), 0)
  })

  it('drains late arrivals during async resolver await without stranding (W1)', async () => {
    let finishModal: (val: any) => void
    let finishResolver: () => void
    let resolverStarted: () => void

    const entered = new Promise<void>(resolve => {
      resolverStarted = resolve
    })

    latch.setAuthStateResolver(async () => {
      resolverStarted()
      await new Promise<void>(resolve => {
        finishResolver = resolve
      })
    })

    const first = latch.triggerReauth(
      'conn:alpha::1',
      () =>
        new Promise(resolve => {
          finishModal = resolve
        })
    )

    const second = latch.triggerReauth('conn:alpha::2', async () => ({ ok: true, connected: true }))

    finishModal!({ ok: true, connected: true })
    await entered

    let lateSettled = false
    const late = latch.triggerReauth('conn:beta::1', async () => ({ ok: true, connected: true }))
    late.then(
      () => {
        lateSettled = true
      },
      () => {
        lateSettled = true
      }
    )

    assert.equal(lateSettled, false)
    assert.equal(latch.isModalActive(), true)

    finishResolver!()

    await Promise.all([first, second, late])
    await new Promise(resolve => setTimeout(resolve, 10))

    assert.equal(lateSettled, true)
    assert.equal(latch.getWaitingCount(), 0)
    assert.equal(latch.isModalActive(), false)
    assert.equal(latch.getAuthState('conn:beta::1')?.status, 'authenticated')
  })

  it('handles rejected resolver cleanly: marks unauthenticated, rejects, and drains next (W1)', async () => {
    latch.setAuthStateResolver(async connectionKey => {
      if (connectionKey === 'conn:fail::2') {
        throw new Error('Resolver explosion')
      }
    })

    let resolveFirst: (val: any) => void

    const p1 = latch.triggerReauth(
      'conn:fail::1',
      () =>
        new Promise(resolve => {
          resolveFirst = resolve
        })
    )

    const p2 = latch.triggerReauth('conn:fail::2', async () => ({ ok: true, connected: true }))
    const p3 = latch.triggerReauth('conn:other::1', async () => ({ ok: true, connected: true, other: true }))

    resolveFirst!({ ok: true, connected: true })

    const res1 = await p1
    assert.deepEqual(res1, { ok: true, connected: true })
    assert.equal(latch.getAuthState('conn:fail::1')?.status, 'authenticated')

    await assert.rejects(p2, { message: 'Resolver explosion' })
    assert.equal(latch.getAuthState('conn:fail::2')?.status, 'unauthenticated')
    assert.equal(latch.getAuthState('conn:fail::2')?.error, 'Resolver explosion')

    const res3 = await p3
    assert.deepEqual(res3, { ok: true, connected: true, other: true })
    assert.equal(latch.getAuthState('conn:other::1')?.status, 'authenticated')
  })
})
