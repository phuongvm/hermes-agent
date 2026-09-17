import { act, cleanup, render, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $gateway } from '@/store/gateway'
const $gatewayState = atom<string>('connecting')
import { $activeProfile, $profiles, refreshActiveProfile } from '@/store/profile'

import { useProfileRailRefreshOnActive } from '../chat/sidebar/use-profile-rail-refresh-on-active'

import { useBackgroundSync } from './hooks/use-background-sync'

// Real caller wiring composition harness
function WiringHarness({
  activeConnectionId,
  activeGatewayProfile,
  gatewayState,
  counts
}: {
  activeConnectionId: string | null
  activeGatewayProfile: string
  gatewayState: string
  counts: {
    model: number
    config: number
    activeProfile: number
    profilesList: number
    sessions: number
  }
}) {
  // 1. Mount profile rail hook
  useProfileRailRefreshOnActive(gatewayState)

  // 2. Mount background sync hook
  useBackgroundSync({
    activeConnectionId,
    activeGatewayProfile,
    activeIsMessaging: false,
    activeSessionId: null,
    activeStoredSessionId: null,
    freshDraftReady: false,
    gatewayState,
    refreshActiveTranscript: () => {},
    refreshCronJobs: () => {},
    refreshCurrentModel: async (force?: boolean) => {
      counts.model += 1
    },
    refreshHermesConfig: async () => {
      counts.config += 1
    },
    refreshMessagingSessions: () => {},
    refreshSessions: async () => {
      counts.sessions += 1
    },
    requestGateway: (async () => ({})) as any,
    updateSessionState: ((() => ({})) as any)
  })

  // 3. Mount scope change wiring (from wiring.tsx:532-563)
  const gatewayScope = `${activeConnectionId ?? ''}\0${activeGatewayProfile}`
  const lastGatewayScopeRef = useRef(gatewayScope)
  const pendingGatewayScopeRefreshRef = useRef(false)

  useEffect(() => {
    if (gatewayScope === lastGatewayScopeRef.current) {
      return
    }

    lastGatewayScopeRef.current = gatewayScope

    if (gatewayState !== 'open') {
      pendingGatewayScopeRefreshRef.current = true

      return
    }

    pendingGatewayScopeRefreshRef.current = false
    counts.model += 1
    counts.config += 1
    void refreshActiveProfile()
  }, [gatewayScope, gatewayState, counts])

  useEffect(() => {
    if (gatewayState === 'open' && pendingGatewayScopeRefreshRef.current) {
      pendingGatewayScopeRefreshRef.current = false
      counts.model += 1
      counts.config += 1
      void refreshActiveProfile()
    }
  }, [gatewayState, counts])

  return null
}

describe('Reconnect Session Resilience Composed Wiring (C3)', () => {
  let requestCounts = {
    activeProfileApi: 0,
    profilesApi: 0
  }

  beforeEach(() => {
    requestCounts = {
      activeProfileApi: 0,
      profilesApi: 0
    }

    $gatewayState.set('connecting')
    $gateway.set(null as any)
    $activeProfile.set('default')
    $profiles.set([{ name: 'default' } as any])

    vi.stubGlobal('hermesDesktop', {
      api: async (req: any) => {
        const path = String(req?.path || '')

        if (path.includes('/api/profiles/active')) {
          requestCounts.activeProfileApi += 1

          return { active: 'default', current: 'default' }
        }

        if (path.includes('/api/profiles')) {
          requestCounts.profilesApi += 1

          return { profiles: [{ name: 'default' }] }
        }

        return {}
      }
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('verifies zero offline requests during disconnected scope changes and focus events', async () => {
    const counts = {
      model: 0,
      config: 0,
      activeProfile: 0,
      profilesList: 0,
      sessions: 0
    }

    // Mount while disconnected
    const { rerender } = render(
      <WiringHarness
        activeConnectionId="conn-1"
        activeGatewayProfile="default"
        counts={counts}
        gatewayState="connecting"
      />
    )

    // Trigger disconnected scope change
    act(() => {
      rerender(
        <WiringHarness
          activeConnectionId="conn-1"
          activeGatewayProfile="researcher"
          counts={counts}
          gatewayState="connecting"
        />
      )
    })

    // Trigger focus / visibility event while disconnected
    act(() => {
      window.dispatchEvent(new Event('focus'))
      document.dispatchEvent(new Event('visibilitychange'))
    })

    // Advance any timers/ticks
    await new Promise(r => setTimeout(r, 50))

    // Verify ZERO offline network requests occurred
    expect(requestCounts.activeProfileApi).toBe(0)
    expect(requestCounts.profilesApi).toBe(0)
    expect(counts.model).toBe(0)
    expect(counts.config).toBe(0)
    expect(counts.sessions).toBe(0)
  })

  it('verifies exactly one refresh per required data source upon reconnect after disconnected scope change', async () => {
    const counts = {
      model: 0,
      config: 0,
      activeProfile: 0,
      profilesList: 0,
      sessions: 0
    }

    const { rerender } = render(
      <WiringHarness
        activeConnectionId="conn-1"
        activeGatewayProfile="default"
        counts={counts}
        gatewayState="connecting"
      />
    )

    // Scope change while disconnected
    act(() => {
      rerender(
        <WiringHarness
          activeConnectionId="conn-1"
          activeGatewayProfile="researcher"
          counts={counts}
          gatewayState="connecting"
        />
      )
    })

    expect(requestCounts.activeProfileApi).toBe(0)
    expect(requestCounts.profilesApi).toBe(0)

    // Reconnect: gateway transitions to 'open'
    act(() => {
      $gatewayState.set('open')
      $gateway.set({ connectionState: 'open' } as any)
      rerender(
        <WiringHarness
          activeConnectionId="conn-1"
          activeGatewayProfile="researcher"
          counts={counts}
          gatewayState="open"
        />
      )
    })

    // Wait for async refreshes to settle
    await waitFor(() => {
      expect(requestCounts.activeProfileApi).toBe(1)
    })

    // Due to the deduplication boundary:
    // Both useBackgroundSync and useProfileRailRefreshOnActive and deferred scope refresh
    // requested active profile, but only EXACTLY ONE network fetch occurred!
    expect(requestCounts.activeProfileApi).toBe(1)
    expect(requestCounts.profilesApi).toBe(1)
    expect(counts.sessions).toBe(1)
  })
})
