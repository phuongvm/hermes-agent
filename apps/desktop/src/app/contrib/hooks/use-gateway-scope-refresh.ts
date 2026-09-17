import { useEffect, useRef } from 'react'

export interface GatewayScopeRefreshParams {
  activeConnectionId: string | null
  activeGatewayProfile: string
  gatewayState: string
  refreshCurrentModel: (force?: boolean) => Promise<unknown> | unknown
  refreshHermesConfig: (force?: boolean) => Promise<unknown> | unknown
  refreshActiveProfile: () => Promise<unknown> | unknown
  onScopeChanged?: () => void
}

/**
 * Coordinates scope-forced and reconnect refresh ownership for model, config,
 * and profile state.
 *
 * When the active backend scope changes (connection or profile):
 * - If gateway is 'open', immediately fires forced refreshes.
 * - If gateway is not 'open', suppresses requests and defers them.
 *
 * When the gateway transitions to 'open' with a pending deferred refresh:
 * - Emits exactly one forced refresh sequence and clears the pending flag.
 */
export function useGatewayScopeRefresh({
  activeConnectionId,
  activeGatewayProfile,
  gatewayState,
  refreshCurrentModel,
  refreshHermesConfig,
  refreshActiveProfile,
  onScopeChanged
}: GatewayScopeRefreshParams): {
  isPendingRefresh: () => boolean
} {
  const gatewayScope = `${activeConnectionId ?? ''}\0${activeGatewayProfile}`
  const lastGatewayScopeRef = useRef(gatewayScope)
  const pendingGatewayScopeRefreshRef = useRef(false)

  useEffect(() => {
    if (gatewayScope === lastGatewayScopeRef.current) {
      return
    }

    lastGatewayScopeRef.current = gatewayScope
    onScopeChanged?.()

    if (gatewayState !== 'open') {
      pendingGatewayScopeRefreshRef.current = true

      return
    }

    pendingGatewayScopeRefreshRef.current = false
    void refreshCurrentModel(true)
    void refreshHermesConfig(true)
    void refreshActiveProfile()
  }, [
    gatewayScope,
    gatewayState,
    onScopeChanged,
    refreshActiveProfile,
    refreshCurrentModel,
    refreshHermesConfig
  ])

  useEffect(() => {
    if (gatewayState === 'open' && pendingGatewayScopeRefreshRef.current) {
      pendingGatewayScopeRefreshRef.current = false
      void refreshCurrentModel(true)
      void refreshHermesConfig(true)
      void refreshActiveProfile()
    }
  }, [
    gatewayState,
    refreshActiveProfile,
    refreshCurrentModel,
    refreshHermesConfig
  ])

  return {
    isPendingRefresh: () => pendingGatewayScopeRefreshRef.current
  }
}
