import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { refreshActiveProfile } = vi.hoisted(() => ({
  refreshActiveProfile: vi.fn().mockResolvedValue(undefined)
}))

vi.mock('@/store/profile', () => ({ refreshActiveProfile }))

import { $gatewayState } from '@/store/session'
import { useProfileRailRefreshOnActive } from './use-profile-rail-refresh-on-active'

describe('useProfileRailRefreshOnActive', () => {
  beforeEach(() => {
    $gatewayState.set('open')
  })

  afterEach(() => {
    refreshActiveProfile.mockClear()
    vi.restoreAllMocks()
  })

  it('refreshes once on mount when gateway is open', () => {
    renderHook(() => useProfileRailRefreshOnActive())

    expect(refreshActiveProfile).toHaveBeenCalledTimes(1)
  })

  it('refreshes again when the window regains focus', async () => {
    renderHook(() => useProfileRailRefreshOnActive())
    refreshActiveProfile.mockClear()

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
    })

    expect(refreshActiveProfile).toHaveBeenCalledTimes(1)
  })

  it('refreshes when visibilitychange fires while the document is visible', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    renderHook(() => useProfileRailRefreshOnActive())
    refreshActiveProfile.mockClear()

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(refreshActiveProfile).toHaveBeenCalledTimes(1)
  })

  it('does NOT refresh when visibilitychange fires while the document is hidden', async () => {
    // Backgrounding/tab-switching away must not trigger a redundant refresh
    // -- only becoming visible/focused again should.
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    renderHook(() => useProfileRailRefreshOnActive())
    refreshActiveProfile.mockClear()

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(refreshActiveProfile).not.toHaveBeenCalled()
  })

  it('removes both listeners on unmount, so a later focus/visibilitychange event does not refresh', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    const { unmount } = renderHook(() => useProfileRailRefreshOnActive())
    refreshActiveProfile.mockClear()

    unmount()

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(refreshActiveProfile).not.toHaveBeenCalled()
  })

  it('does not accumulate listeners across repeated mount/unmount cycles', async () => {
    // A leaked listener from a prior mount would double- (or N-times-)
    // refresh on a single focus event after remounting.
    const first = renderHook(() => useProfileRailRefreshOnActive())
    first.unmount()

    renderHook(() => useProfileRailRefreshOnActive())
    refreshActiveProfile.mockClear()

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
    })

    expect(refreshActiveProfile).toHaveBeenCalledTimes(1)
  })

  it('does NOT refresh on mount when gateway is in connecting state', () => {
    $gatewayState.set('connecting')
    renderHook(() => useProfileRailRefreshOnActive())

    expect(refreshActiveProfile).not.toHaveBeenCalled()
  })

  it('does NOT refresh when window regains focus while gateway is in connecting state', async () => {
    $gatewayState.set('connecting')
    renderHook(() => useProfileRailRefreshOnActive())
    refreshActiveProfile.mockClear()

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
    })

    expect(refreshActiveProfile).not.toHaveBeenCalled()
  })

  it('does NOT refresh when visibilitychange fires while gateway is in connecting state', async () => {
    $gatewayState.set('connecting')
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    renderHook(() => useProfileRailRefreshOnActive())
    refreshActiveProfile.mockClear()

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(refreshActiveProfile).not.toHaveBeenCalled()
  })

  it('refreshes once on reconnect when gateway transitions from connecting to open', async () => {
    $gatewayState.set('connecting')
    renderHook(() => useProfileRailRefreshOnActive())

    expect(refreshActiveProfile).not.toHaveBeenCalled()

    await act(async () => {
      $gatewayState.set('open')
    })

    expect(refreshActiveProfile).toHaveBeenCalledTimes(1)
  })

  it('defers focus refresh during disconnect and executes single coalesced refresh on reconnect', async () => {
    $gatewayState.set('connecting')
    renderHook(() => useProfileRailRefreshOnActive())
    expect(refreshActiveProfile).not.toHaveBeenCalled()

    // Multiple focus events while disconnected
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      window.dispatchEvent(new Event('focus'))
    })
    expect(refreshActiveProfile).not.toHaveBeenCalled()

    // Reconnect to open -> single coalesced refresh
    await act(async () => {
      $gatewayState.set('open')
    })
    expect(refreshActiveProfile).toHaveBeenCalledTimes(1)
  })
})
