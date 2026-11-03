import { isGatewayReauthRequired } from '@hermes/shared'

import { isRemoteReauthError } from '@/components/boot-failure-reauth'

/** A gateway dial was rejected for want of a signed-in session. Electron IPC
 *  (`hermes:connection:for`) structured-clones the rejection and strips
 *  `needsOauthLogin` / the error class, so the message is checked as well.
 *  Retrying cannot fix this; only the user signing in can. */
export function isGatewayReauthError(error: unknown): boolean {
  if (isGatewayReauthRequired(error)) {
    return true
  }

  const message = error instanceof Error ? error.message : String(error ?? '')

  return isRemoteReauthError(message) || (message.includes('401') && message.toLowerCase().includes('no_cookie'))
}
