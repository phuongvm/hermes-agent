import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import {
  $terminalSignedOutUrls,
  isTerminalSignedOut,
  normalizeBaseUrl,
  registerResumeSyncHandler
} from '@/store/auth-terminal-state'
import { refreshActiveProfile } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

/**
 * Re-pull the running profile + list on mount, and again whenever the window
 * regains focus/visibility -- a profile created, deleted, or renamed by
 * another surface (Manage Profiles, another window, the CLI) leaves the
 * rail's cached $profiles stale until something re-fetches it. Without this,
 * a deleted profile's square lingers in the rail until the user happens to
 * open Manage Profiles (whose own refresh() call was the only other reader).
 *
 * Suppressed when gateway state is not 'open' (e.g. 'connecting', 'closed',
 * 'idle') or when the active connection is in terminal signed-out state to
 * prevent IPC timeout / network storms.
 * Any suppressed refresh is deferred and coalesced into a single refresh
 * when the gateway transitions back to 'open' and authenticated.
 */
export function useProfileRailRefreshOnActive(gatewayStateOverride?: string): void {
  const storeGatewayState = useStore($gatewayState)
  const gatewayState = gatewayStateOverride ?? storeGatewayState
  const connection = useStore($connection)
  const signedOutUrls = useStore($terminalSignedOutUrls)
  const isSignedOut = isTerminalSignedOut(connection?.baseUrl)

  const prevGatewayStateRef = useRef<string | null>(null)
  const prevSignedOutRef = useRef<boolean | null>(null)
  const hasDeferredRefreshRef = useRef(false)

  useEffect(() => {
    return registerResumeSyncHandler(baseUrl => {
      const currentBaseUrl = normalizeBaseUrl($connection.get()?.baseUrl || '')

      if (currentBaseUrl === normalizeBaseUrl(baseUrl) && $gatewayState.get() === 'open') {
        hasDeferredRefreshRef.current = false
        void refreshActiveProfile()
      }
    })
  }, [])

  useEffect(() => {
    const isInitial = prevGatewayStateRef.current === null
    const prevGateway = prevGatewayStateRef.current
    const prevSignedOut = prevSignedOutRef.current

    prevGatewayStateRef.current = gatewayState
    prevSignedOutRef.current = isSignedOut

    if (gatewayState === 'open' && !isSignedOut) {
      if (isInitial || prevGateway !== 'open' || prevSignedOut || hasDeferredRefreshRef.current) {
        hasDeferredRefreshRef.current = false
        void refreshActiveProfile()
      }
    } else {
      if (isInitial || isSignedOut) {
        hasDeferredRefreshRef.current = true
      }
    }
  }, [gatewayState, isSignedOut, signedOutUrls])

  useEffect(() => {
    const onActive = () => {
      if (document.visibilityState === 'hidden') {
        return
      }

      if (gatewayState !== 'open' || isTerminalSignedOut(connection?.baseUrl)) {
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
  }, [gatewayState, connection?.baseUrl])
}
