import { GatewayReauthRequiredError } from '@hermes/shared'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { DesktopConnectionsRegistry } from '@/global'

const released: string[] = []
const keepRoute = vi.fn((connectionId: string, _profile: string) => () => released.push(connectionId))
const openGatewayForAgent = vi.fn(async (_connectionId: string, _profile: string) => undefined)
const live = new Set<string>()
// `live`: the gateway's kept default-profile route is open. This set: only a
// named profile's socket on that gateway is open.
const liveNamedProfile = new Set<string>()

vi.mock('@/store/gateway', () => ({
  gatewayRouteState: (connectionId: string, profile: string) => ({
    key: `${connectionId}:${profile}`,
    open: profile === 'default' && live.has(connectionId)
  }),
  // Not read by the keeper; the pre-fix keeper did, so the redial test fails there on its assertion.
  liveSecondaryConnectionIds: () => new Set([...live, ...liveNamedProfile]),
  openGatewayForAgent
}))
vi.mock('@/store/gateway-kept-routes', () => ({ keepRoute }))

const { $connectionsRegistry } = await import('@/store/connection-registry-state')
const { $primaryGatewayRoute } = await import('@/store/gateway-primary-route')
const { startPersistentGateways } = await import('./gateway-persistence')

const registry = (ids: string[]): DesktopConnectionsRegistry => ({
  connections: ids.map(id => ({
    id,
    kind: id === 'local' ? 'local' : 'remote',
    label: id,
    tokenPreview: null,
    tokenSet: false
  })),
  primary: 'local',
  secureTokenStorage: true,
  version: 2
})

let stop: () => void = () => undefined

function installProbe(bootstrapNeeded: boolean) {
  const probeLocalBackend = vi.fn(async () => ({ bootstrapNeeded }))

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { probeLocalBackend }

  return probeLocalBackend
}

beforeEach(() => {
  vi.useFakeTimers()
  released.length = 0
  live.clear()
  liveNamedProfile.clear()
  keepRoute.mockClear()
  openGatewayForAgent.mockReset()
  openGatewayForAgent.mockResolvedValue(undefined)
  $primaryGatewayRoute.set({ connectionId: 'local', mode: 'local' })
  $connectionsRegistry.set(registry(['local', 'homelab', 'vps']))
})

afterEach(() => {
  stop()
  vi.useRealTimers()
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

it('keeps every registered gateway the primary socket does not serve connected, and lets a removed one go', () => {
  stop = startPersistentGateways()

  expect(keepRoute.mock.calls).toEqual([
    ['homelab', 'default'],
    ['vps', 'default']
  ])
  expect(openGatewayForAgent.mock.calls).toEqual([
    ['homelab', 'default'],
    ['vps', 'default']
  ])

  // A dropped socket is redialed on the next tick; a live one is left alone.
  live.add('homelab')
  openGatewayForAgent.mockClear()
  vi.advanceTimersByTime(30_000)
  expect(openGatewayForAgent.mock.calls).toEqual([['vps', 'default']])

  $connectionsRegistry.set(registry(['local', 'homelab']))
  expect(released).toEqual(['vps'])
  expect(keepRoute).toHaveBeenCalledTimes(2)
})

it('with a remote primary, keeps This device only where Hermes is installed (never starts the installer)', async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  installProbe(true)
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)

  expect(openGatewayForAgent.mock.calls).toEqual([['vps', 'default']])

  stop()
  openGatewayForAgent.mockClear()
  installProbe(false)
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)

  expect(openGatewayForAgent.mock.calls).toEqual([
    ['vps', 'default'],
    ['local', 'default']
  ])
})

it('redials a dropped kept route even while a named profile on the same gateway stays open', () => {
  live.add('homelab')
  live.add('vps')
  stop = startPersistentGateways()
  expect(openGatewayForAgent).not.toHaveBeenCalled()

  live.delete('homelab')
  liveNamedProfile.add('homelab')
  vi.advanceTimersByTime(30_000)

  expect(dialedRoutes()).toEqual([['homelab', 'default']])
})

/** (connection, profile) per dial, whatever options the keeper passes. */
function dialedRoutes(): unknown[][] {
  return openGatewayForAgent.mock.calls.map(call => call.slice(0, 2))
}

it("dials nothing while no primary route is published, so the primary's own gateway never gets a second socket", () => {
  $primaryGatewayRoute.set({ connectionId: null, mode: null })
  stop = startPersistentGateways()
  vi.advanceTimersByTime(30_000)
  expect(openGatewayForAgent).not.toHaveBeenCalled()

  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  expect(dialedRoutes()).toEqual([['vps', 'default']])

  // A primary teardown clears the route until the next primary publishes.
  openGatewayForAgent.mockClear()
  $primaryGatewayRoute.set({ connectionId: null, mode: null })
  vi.advanceTimersByTime(30_000)
  expect(openGatewayForAgent).not.toHaveBeenCalled()
})

it('probes This device once while it stays not installed, and again after the registry changes', async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  const probe = installProbe(true)
  stop = startPersistentGateways()

  await vi.advanceTimersByTimeAsync(5 * 60_000)
  expect(probe).toHaveBeenCalledTimes(1)

  $connectionsRegistry.set(registry(['local', 'homelab', 'vps', 'nas']))
  await vi.advanceTimersByTimeAsync(0)
  expect(probe).toHaveBeenCalledTimes(2)
  expect(dialedRoutes()).not.toContainEqual(['local', 'default'])
})

it('re-probes This device on a new primary route, not on each reconnect that republishes the same one', async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  const probe = installProbe(true)
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)

  for (let reconnect = 0; reconnect < 3; reconnect += 1) {
    $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
    await vi.advanceTimersByTimeAsync(0)
  }

  expect(probe).toHaveBeenCalledTimes(1)

  $primaryGatewayRoute.set({ connectionId: 'vps', mode: 'remote' })
  await vi.advanceTimersByTimeAsync(0)
  expect(probe).toHaveBeenCalledTimes(2)
})

it('a dropped probe that fails late leaves the newer cached probe in place', async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  let failFirst: (error: Error) => void = () => undefined

  const probeLocalBackend = vi
    .fn<() => Promise<{ bootstrapNeeded: boolean }>>()
    .mockImplementationOnce(() => new Promise((_resolve, reject) => (failFirst = reject)))
    .mockImplementation(async () => ({ bootstrapNeeded: true }))

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { probeLocalBackend }
  stop = startPersistentGateways()

  // The registry change drops the in-flight probe and starts a second one.
  $connectionsRegistry.set(registry(['local', 'homelab', 'vps', 'nas']))
  await vi.advanceTimersByTimeAsync(0)
  expect(probeLocalBackend).toHaveBeenCalledTimes(2)

  failFirst(new Error('probe failed'))
  await vi.advanceTimersByTimeAsync(5 * 60_000)
  expect(probeLocalBackend).toHaveBeenCalledTimes(2)
})

it("a dropped probe's late installed answer neither clears its replacement's not-installed verdict nor dials", async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  let answerFirst: (result: { bootstrapNeeded: boolean }) => void = () => undefined

  const probeLocalBackend = vi
    .fn<() => Promise<{ bootstrapNeeded: boolean }>>()
    .mockImplementationOnce(() => new Promise(resolve => (answerFirst = resolve)))
    .mockImplementation(async () => ({ bootstrapNeeded: true }))

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { probeLocalBackend }
  stop = startPersistentGateways()

  // The registry change drops the in-flight probe; its replacement finds no install.
  $connectionsRegistry.set(registry(['local', 'homelab', 'vps', 'nas']))
  await vi.advanceTimersByTimeAsync(0)
  expect(probeLocalBackend).toHaveBeenCalledTimes(2)

  answerFirst({ bootstrapNeeded: false })
  await vi.advanceTimersByTimeAsync(5 * 60_000)
  expect(probeLocalBackend).toHaveBeenCalledTimes(2)
  // Nor does it let its own waiting dial reach an install the newer probe found missing.
  expect(dialedRoutes()).not.toContainEqual(['local', 'default'])
})

it('re-probes a missing This device install every ten minutes, so one installed meanwhile is kept', async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })

  const probeLocalBackend = vi
    .fn<() => Promise<{ bootstrapNeeded: boolean }>>()
    .mockImplementationOnce(async () => ({ bootstrapNeeded: true }))
    .mockImplementation(async () => ({ bootstrapNeeded: false }))

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { probeLocalBackend }
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(9 * 60_000)
  expect(probeLocalBackend).toHaveBeenCalledTimes(1)
  expect(dialedRoutes()).not.toContainEqual(['local', 'default'])

  await vi.advanceTimersByTimeAsync(75_000)
  expect(probeLocalBackend).toHaveBeenCalledTimes(2)
  expect(dialedRoutes()).toContainEqual(['local', 'default'])
})

it('re-checks an installed This device before each redial, so an install removed since is never dialed', async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })

  const probeLocalBackend = vi
    .fn<() => Promise<{ bootstrapNeeded: boolean }>>()
    .mockImplementationOnce(async () => ({ bootstrapNeeded: false }))
    .mockImplementation(async () => ({ bootstrapNeeded: true }))

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { probeLocalBackend }
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)
  expect(dialedRoutes()).toContainEqual(['local', 'default'])

  // The local route never opened and the install is gone by the next tick.
  openGatewayForAgent.mockClear()
  await vi.advanceTimersByTimeAsync(30_000)

  expect(probeLocalBackend).toHaveBeenCalledTimes(2)
  expect(dialedRoutes()).not.toContainEqual(['local', 'default'])
})

it('drops a This device dial still waiting on its probe once the primary route is unpublished or the keeper stops', async () => {
  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  let answer: (result: { bootstrapNeeded: boolean }) => void = () => undefined

  const probeLocalBackend = vi.fn(() => new Promise<{ bootstrapNeeded: boolean }>(resolve => (answer = resolve)))

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { probeLocalBackend }
  stop = startPersistentGateways()
  $primaryGatewayRoute.set({ connectionId: null, mode: null })
  answer({ bootstrapNeeded: false })
  await vi.advanceTimersByTimeAsync(0)
  expect(dialedRoutes()).not.toContainEqual(['local', 'default'])

  $primaryGatewayRoute.set({ connectionId: 'homelab', mode: 'remote' })
  stop()
  answer({ bootstrapNeeded: false })
  await vi.advanceTimersByTimeAsync(0)
  expect(dialedRoutes()).not.toContainEqual(['local', 'default'])
})

const IPC_REAUTH = new Error(
  `Error invoking remote method 'hermes:connection:for': Error: Remote Hermes gateway uses OAuth, but you are not signed in. Open Settings → Gateway and click "Sign in", or switch back to Local.`
)

it('keeps redialing a transient network failure on every tick', async () => {
  openGatewayForAgent.mockImplementation(async (id: string) => {
    if (id === 'homelab') {
      throw new Error('ECONNREFUSED')
    }
  })
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)
  openGatewayForAgent.mockClear()

  await vi.advanceTimersByTimeAsync(30_000)
  await vi.advanceTimersByTimeAsync(30_000)

  expect(openGatewayForAgent.mock.calls.filter(([id]) => id === 'homelab')).toHaveLength(2)
})

it.each([
  ['GatewayReauthRequiredError', () => new GatewayReauthRequiredError('Gateway sign-in required')],
  ['needsOauthLogin flag', () => Object.assign(new Error('x'), { needsOauthLogin: true })],
  ['IPC-serialized reauth message', () => IPC_REAUTH],
  ['401 no_cookie', () => new Error('ws-ticket failed: 401 no_cookie')]
])('pauses the 30s redial of a gateway that rejects with reauth (%s) and keeps dialing the others', async (_n, make) => {
  openGatewayForAgent.mockImplementation(async (id: string) => {
    if (id === 'homelab') {
      throw make()
    }
  })
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)
  expect(openGatewayForAgent.mock.calls.filter(([id]) => id === 'homelab')).toHaveLength(1)
  openGatewayForAgent.mockClear()

  await vi.advanceTimersByTimeAsync(30_000)
  await vi.advanceTimersByTimeAsync(30_000)

  expect(openGatewayForAgent.mock.calls.filter(([id]) => id === 'homelab')).toHaveLength(0)
  expect(openGatewayForAgent.mock.calls.filter(([id]) => id === 'vps')).toHaveLength(2)
})

it('resumes dialing a reauth-paused gateway immediately when the registry updates on sign in', async () => {
  let signedIn = false
  openGatewayForAgent.mockImplementation(async (id: string) => {
    if (id === 'homelab' && !signedIn) {
      throw new GatewayReauthRequiredError('Gateway sign-in required')
    }
  })
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)
  await vi.advanceTimersByTimeAsync(30_000)
  openGatewayForAgent.mockClear()

  signedIn = true
  $connectionsRegistry.set(registry(['local', 'homelab', 'vps']))
  await vi.advanceTimersByTimeAsync(0)

  expect(openGatewayForAgent.mock.calls.filter(([id]) => id === 'homelab')).toHaveLength(1)
})

it('re-pauses when the redial after a registry update is rejected with reauth again', async () => {
  openGatewayForAgent.mockImplementation(async (id: string) => {
    if (id === 'homelab') {
      throw IPC_REAUTH
    }
  })
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)
  $connectionsRegistry.set(registry(['local', 'homelab', 'vps']))
  await vi.advanceTimersByTimeAsync(0)
  openGatewayForAgent.mockClear()

  await vi.advanceTimersByTimeAsync(60_000)

  expect(openGatewayForAgent.mock.calls.filter(([id]) => id === 'homelab')).toHaveLength(0)
})


it('ignores a reauth rejection from a dial that started before the registry update (sign in wins)', async () => {
  let rejectStale: (error: Error) => void = () => undefined
  let homelabCalls = 0
  openGatewayForAgent.mockImplementation((id: string) => {
    if (id === 'homelab' && ++homelabCalls === 1) {
      return new Promise<undefined>((_resolve, reject) => {
        rejectStale = reject
      })
    }

    return Promise.resolve(undefined)
  })
  stop = startPersistentGateways()
  await vi.advanceTimersByTimeAsync(0)

  // Sign in re-arms while the pre-sign-in dial is still pending.
  $connectionsRegistry.set(registry(['local', 'homelab', 'vps']))
  await vi.advanceTimersByTimeAsync(0)
  expect(homelabCalls).toBe(2)

  // The stale attempt now fails; it must not re-park the gateway.
  rejectStale(new GatewayReauthRequiredError('Gateway sign-in required'))
  await vi.advanceTimersByTimeAsync(0)
  openGatewayForAgent.mockClear()
  await vi.advanceTimersByTimeAsync(30_000)

  expect(openGatewayForAgent.mock.calls.filter(([id]) => id === 'homelab')).toHaveLength(1)
})
