/**
 * `host.openSession` from a plugin page mounted as a CENTER route tile
 * (`route-tile:/crew`) — "Open chat" on a Crew card.
 *
 * The core open writes the hash and fronts main only when
 * `$selectedStoredSessionId` already names the session; the HashRouter
 * hashchange that updates it lands asynchronously, so the route tile stayed in
 * front and the opened chat loaded invisibly behind it. A main-bound open
 * (`in-place` / `main`) therefore reveals the `workspace` pane itself. An
 * open the session's own tile already holds — or one bound for a new
 * tab/stack/window — must not yank the user to main.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/app/chat/session-view', async () => {
  const { atom } = await import('nanostores')

  return { PRIMARY_SESSION_VIEW: { $awaitingResponse: atom(false), $busy: atom(false) } }
})
vi.mock('@/app/open-session', () => ({ openSession: vi.fn() }))
vi.mock('@/components/pane-shell/tree/store', async () => {
  const { atom } = await import('nanostores')

  // session-focus.ts (reached via the preview store) and the layout store
  // read these at import time; a mock without them crashes before any test.
  return {
    $activeTreeGroup: atom(null),
    $collapsedTreeSides: atom(new Set()),
    $hiddenTreePanes: atom(new Set()),
    $layoutTree: atom(null),
    $narrowViewport: atom(false),
    revealTreePane: vi.fn()
  }
})
vi.mock('@/contrib/events', () => ({ onGatewayEvent: vi.fn() }))
vi.mock('@/hermes', () => ({ deleteProfile: vi.fn(), getLogs: vi.fn(), getStatus: vi.fn(), hermesApi: vi.fn() }))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))
vi.mock('@/store/system-actions', () => ({ runGatewayRestart: vi.fn() }))
vi.mock('@/store/session', async () => {
  const { atom } = await import('nanostores')

  type LineageRow = { _lineage_root_id?: null | string; id: string }

  return {
    $activeSessionId: atom(null),
    $connection: atom(null),
    $cronSessions: atom([]),
    $currentCwd: atom(''),
    $currentModel: atom(''),
    $gatewayState: atom('open'),
    $messages: atom([]),
    $messagingSessions: atom([]),
    $selectedStoredSessionId: atom(null),
    $sessions: atom([]),
    $unreadFinishedSessionIds: atom([]),
    lineageAliases: (storedId: string) => [storedId],
    rememberedSessionProfile: (_sessions: unknown, _sessionId: null | string, activeProfile: null | string) =>
      (activeProfile ?? '').trim() || 'default',
    requestSessionResume: vi.fn(),
    sessionMatchesStoredId: (session: LineageRow, storedSessionId: string) =>
      session.id === storedSessionId || session._lineage_root_id === storedSessionId,
    sessionPinId: (session: LineageRow) => session._lineage_root_id ?? session.id,
    setSessionOwnerHint: vi.fn(),
    setResumeExhaustedSessionId: vi.fn()
  }
})
vi.mock('@/store/session-states', async () => {
  const { atom } = await import('nanostores')

  return {
    $attentionSessionIds: atom([]),
    $draftSessionIds: atom([]),
    $focusedRuntimeId: atom(null),
    $focusedSessionState: atom(null),
    $sessionTiles: atom([]),
    $sessionStates: atom({}),
    $stalledSessionIds: atom([]),
    $workingSessionIds: atom([]),
    dropTilesForProfile: vi.fn(),
    sessionTileDelegate: vi.fn(() => null)
  }
})
vi.mock('@/store/profile', async () => {
  const { atom } = await import('nanostores')

  const profiles = atom([
    {
      has_env: false,
      is_default: false,
      model: null,
      name: 'cached-only',
      path: '/profiles/cached-only',
      provider: null,
      skill_count: 0
    }
  ])

  return {
    $activeGatewayProfile: atom('ops'),
    $gatewaySwapTarget: atom(null),
    $hydrationSyncProfile: atom(null),
    $profiles: profiles,
    $showAllProfiles: atom(false),
    ensureGatewayAgent: vi.fn(),
    ensureGatewayProfile: vi.fn(),
    newSessionInAgent: vi.fn(),
    newSessionInProfile: vi.fn(),
    normalizeProfileKey: (value: null | string | undefined) => (value ?? '').trim() || 'default',
    refreshProfiles: vi.fn(async () => profiles.get()),
    selectProfile: vi.fn(),
    setActiveProfile: vi.fn(),
    setShowAllProfiles: vi.fn()
  }
})
vi.mock('@/store/gateway', async () => {
  const { atom } = await import('nanostores')

  return {
    $activeGatewayRoute: atom('default'),
    $gateway: atom(null),
    activeGateway: vi.fn(() => null),
    activeGatewayConnectionId: vi.fn(() => 'local'),
    ensureGatewayForAgent: vi.fn(),
    openGatewayForAgent: vi.fn(),
    openGatewayForProfile: vi.fn(),
    requestGatewayForAgent: vi.fn(),
    requestGatewayForProfile: vi.fn(),
    retainGatewayForAgent: vi.fn(async () => vi.fn()),
    retireLocalProfileGateways: vi.fn()
  }
})


const { host } = await import('./index')

const { openSession: openSessionCore } = await import('@/app/open-session')

const { revealTreePane } = await import('@/components/pane-shell/tree/store')

const { $activeGatewayProfile } = await import('@/store/profile')

const { $sessionTiles } = await import('@/store/session-states')

const setMockAtom = <T>(store: unknown, value: T) => (store as { set(next: T): void }).set(value)

afterEach(() => {
  vi.clearAllMocks()
  setMockAtom($sessionTiles, [])
  $activeGatewayProfile.set('ops')
})

describe('host.openSession from a route tile fronts the workspace pane', () => {
  it.each(['in-place', 'main'] as const)('reveals the workspace pane after a %s open into main', async intent => {
    await host.openSession('chat-1', { intent, profile: 'ops' })

    expect(openSessionCore).toHaveBeenCalledWith('chat-1', expect.any(Function), intent)
    expect(revealTreePane).toHaveBeenCalledWith('workspace')
  })

  it('defaults to the in-place intent and still reveals the workspace pane', async () => {
    await host.openSession('chat-1', { profile: 'ops' })

    expect(openSessionCore).toHaveBeenCalledWith('chat-1', expect.any(Function), 'in-place')
    expect(revealTreePane).toHaveBeenCalledWith('workspace')
  })

  it('leaves the layout alone when the session already lives in its own tile', async () => {
    setMockAtom($sessionTiles, [{ storedSessionId: 'chat-1' }] as never)

    await host.openSession('chat-1', { intent: 'in-place', profile: 'ops' })

    expect(openSessionCore).toHaveBeenCalled()
    expect(revealTreePane).not.toHaveBeenCalledWith('workspace')
  })

  it.each(['tab', 'stack', 'window'] as const)('does not front main for a %s open', async intent => {
    await host.openSession('chat-1', { intent, profile: 'ops' })

    expect(openSessionCore).toHaveBeenCalledWith('chat-1', expect.any(Function), intent)
    expect(revealTreePane).not.toHaveBeenCalledWith('workspace')
  })
})
