import assert from 'node:assert/strict'

import { describe, test, vi } from 'vitest'

import { runPrimaryBackendStartup } from './primary-backend-startup'

describe('5.4: Client-Only Startup Boundary Integration Tests', () => {
  test('(a, b, c, d, e) Seeded remote primary connects remotely and bypasses all local bootstrap/installer paths', async () => {
    // 1. Seeded connections.json represented by resolved remote descriptor
    const seededRemote = {
      baseUrl: 'https://team-gateway.hermes.ai',
      connectionId: 'seeded-team-gateway',
      authMode: 'oauth' as const,
      remoteKind: 'url' as const,
      token: null,
      wsUrl: 'wss://team-gateway.hermes.ai/api/ws'
    }

    const prepareLocalBackend = vi.fn(async () => ({
      kind: 'bootstrap-needed',
      activeRoot: '/fake/root'
    }))

    const ensureLocalRuntime = vi.fn(async (backend: any) => ({
      ...backend,
      command: 'hermes'
    }))

    const downloadUpstreamInstaller = vi.fn(async () => {
      throw new Error('Upstream installer download should never be called for client-only seed!')
    })

    const waitForLocalStart = vi.fn(async () => {})
    const waitForDecision = vi.fn(async () => 'continue-local' as const)

    const connectRemote = vi.fn(async (remote: typeof seededRemote) => ({
      baseUrl: remote.baseUrl,
      mode: 'remote' as const,
      connectionId: remote.connectionId
    }))

    const result = await runPrimaryBackendStartup({
      resolveRemote: vi.fn(async () => seededRemote),
      connectRemote,
      prepareLocalBackend,
      ensureLocalRuntime,
      waitForLocalStart,
      waitForDecision
    })

    // (b) Startup path resolves the saved remote and takes the remote path
    assert.equal(result.kind, 'remote')
    assert.deepEqual(result.connection, {
      baseUrl: 'https://team-gateway.hermes.ai',
      mode: 'remote',
      connectionId: 'seeded-team-gateway'
    })
    assert.equal(connectRemote.mock.calls.length, 1)
    assert.deepEqual(connectRemote.mock.calls[0], [seededRemote])

    // (c) Verifies prepareLocalBackend is NOT called
    assert.equal(prepareLocalBackend.mock.calls.length, 0)

    // (d) Verifies ensureLocalRuntime / runBootstrap are NOT invoked
    assert.equal(ensureLocalRuntime.mock.calls.length, 0)
    assert.equal(waitForLocalStart.mock.calls.length, 0)
    assert.equal(waitForDecision.mock.calls.length, 0)

    // (e) Verifies no upstream installer download is attempted
    assert.equal(downloadUpstreamInstaller.mock.calls.length, 0)
  })

  test('Unreachable seeded remote surfaces error and does NOT silently fall through to local bootstrap', async () => {
    const unreachableRemote = {
      baseUrl: 'https://unreachable-gateway.internal',
      connectionId: 'seeded-team-gateway',
      authMode: 'oauth' as const,
      token: null,
      wsUrl: 'wss://unreachable-gateway.internal/api/ws'
    }

    const prepareLocalBackend = vi.fn(async () => ({ kind: 'bootstrap-needed' }))
    const ensureLocalRuntime = vi.fn(async () => ({ command: 'hermes' }))

    const connectRemote = vi.fn(async () => {
      throw new Error('Connection refused (gateway unreachable: 503 Service Unavailable)')
    })

    await assert.rejects(async () => {
      await runPrimaryBackendStartup({
        resolveRemote: vi.fn(async () => unreachableRemote),
        connectRemote,
        prepareLocalBackend,
        ensureLocalRuntime,
        waitForLocalStart: vi.fn(async () => {}),
        waitForDecision: vi.fn(async () => 'continue-local' as const)
      })
    }, /gateway unreachable/)

    // Critical: Fail-closed boundary — MUST NOT fall through to local preparation!
    assert.equal(prepareLocalBackend.mock.calls.length, 0)
    assert.equal(ensureLocalRuntime.mock.calls.length, 0)
  })
})
