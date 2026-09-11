import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { refreshActiveProfile } from '@/store/profile'
import { $gatewayState } from '@/store/session'

/**
 * Re-pull the running profile + list on mount, and again whenever the window
 * regains focus/visibility -- a profile created, deleted, or renamed by
 * another surface (Manage Profiles, another window, the CLI) leaves the
 * rail's cached $profiles stale until something re-fetches it. Without this,
 * a deleted profile's square lingers in the rail until the user happens to
 * open Manage Profiles (whose own refresh() call was the only other reader).
 *
 * Suppressed when gateway state is not 'open' (e.g. 'connecting', 'closed',
 * 'idle') to prevent IPC timeout / network storms during reconnect windows.
 * Any suppressed refresh is deferred and coalesced into a single refresh
 * when the gateway transitions back to 'open'.
 */
export function useProfileRailRefreshOnActive(gatewayStateOverride?: string): void {
  const storeGatewayState = useStore($gatewayState)
  const gatewayState = gatewayStateOverride ?? storeGatewayState
  const prevGatewayStateRef = useRef<string | null>(null)
  const hasDeferredRefreshRef = useRef(false)

  useEffect(() => {
    const isInitial = prevGatewayStateRef.current === null
    const prev = prevGatewayStateRef.current
    prevGatewayStateRef.current = gatewayState

    if (gatewayState === 'open') {
      if (isInitial || prev !== 'open' || hasDeferredRefreshRef.current) {
        hasDeferredRefreshRef.current = false
        void refreshActiveProfile()
      }
    } else {
      if (isInitial) {
        hasDeferredRefreshRef.current = true
      }
    }
  }, [gatewayState])

  useEffect(() => {
    const onActive = () => {
      if (document.visibilityState === 'hidden') {
        return
      }

      if (gatewayState !== 'open') {
        hasDeferredRefreshRef.current = true
        return
      }

      void refreshActiveProfile()
    }

    window.addEventListener('focus', onActive)
    document.addEventListener('visibilitychange', onActive)

    return () => {
      window.removeEventListener('focus', onActive)
      document.removeEventListener('visibilitychange', onActive)
    }
  }, [gatewayState])
}
