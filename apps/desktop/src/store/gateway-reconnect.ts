import { translateNow } from '@/i18n'
import { isTerminalSignedOut, normalizeBaseUrl } from '@/store/auth-terminal-state'

export interface ReconnectOwnerKey {
  connectionId?: string | null
  basePath?: string | null
  profile?: string | null
  windowId?: string | null
  logicalOwner?: string | null
}

export function buildReconnectOwnerKey(key: ReconnectOwnerKey): string {
  const conn = key.connectionId || 'default'
  const endpoint = key.basePath ? normalizeBaseUrl(key.basePath) : 'default'
  const prof = key.profile || 'default'
  const win = key.windowId || 'main'
  const owner = key.logicalOwner || 'primary'
  return `${conn}::${endpoint}::${prof}::${win}::${owner}`
}

type GatewayReconnectHandler = () => Promise<void> | void

const activeHandlers = new Map<string, GatewayReconnectHandler>()
const inFlightPromises = new Map<string, Promise<void>>()

export function registerGatewayReconnect(
  handler: GatewayReconnectHandler,
  key: ReconnectOwnerKey = {}
): () => void {
  const ownerKey = buildReconnectOwnerKey(key)
  activeHandlers.set(ownerKey, handler)

  return () => {
    if (activeHandlers.get(ownerKey) === handler) {
      activeHandlers.delete(ownerKey)
    }
  }
}

export function reconnectGateway(key: ReconnectOwnerKey = {}): Promise<void> {
  if (isTerminalSignedOut(key.basePath)) {
    const err = new Error('Authentication required (signed-out)')
    Object.assign(err, { statusCode: 401, code: 'ERR_SIGNED_OUT' })
    return Promise.reject(err)
  }

  const ownerKey = buildReconnectOwnerKey(key)
  const existing = inFlightPromises.get(ownerKey)
  if (existing) {
    return existing
  }

  const handler = activeHandlers.get(ownerKey)
  if (!handler) {
    return Promise.reject(new Error(`Gateway reconnect is unavailable for ${ownerKey}`))
  }

  const promise = Promise.resolve()
    .then(handler)
    .finally(() => {
      if (inFlightPromises.get(ownerKey) === promise) {
        inFlightPromises.delete(ownerKey)
      }
    })

  inFlightPromises.set(ownerKey, promise)
  return promise
}

/** Toast button that re-dials the active connection — attached wherever a
 *  send fails because Hermes is offline (sudo/secret/approval prompts …). */
export function reconnectAction(): { label: string; onClick: () => void } {
  return {
    label: translateNow('prompts.reconnect'),
    onClick: () => void reconnectGateway().catch(() => undefined)
  }
}
