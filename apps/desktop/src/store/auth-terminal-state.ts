import { atom } from 'nanostores'

import { $connection } from '@/store/session'

export function normalizeBaseUrl(url: string): string {
  const trimmed = String(url || '').trim()

  if (!trimmed) {
    return ''
  }

  try {
    const parsed = new URL(trimmed)

    return `${parsed.protocol}//${parsed.host.toLowerCase()}${parsed.pathname}`.replace(/\/+$/, '')
  } catch {
    return trimmed.replace(/\/+$/, '')
  }
}

export const $terminalSignedOutUrls = atom<string[]>([])

const resumeSyncHandlers = new Set<(baseUrl: string) => void | Promise<void>>()

export function isTerminalSignedOut(url?: string | null): boolean {
  const target = url !== undefined && url !== null ? url : $connection.get()?.baseUrl

  if (!target) {
    return false
  }

  const normalized = normalizeBaseUrl(target)

  return $terminalSignedOutUrls.get().includes(normalized)
}

export function setTerminalSignedOut(url: string, signedOut: boolean, reason?: string): void {
  const normalized = normalizeBaseUrl(url)

  if (!normalized) {
    return
  }

  const current = $terminalSignedOutUrls.get()
  const exists = current.includes(normalized)

  if (signedOut) {
    if (!exists) {
      $terminalSignedOutUrls.set([...current, normalized])
    }
  } else {
    if (exists) {
      $terminalSignedOutUrls.set(current.filter(item => item !== normalized))
      resumeDeferredSync(normalized)
    }
  }
}

export function registerResumeSyncHandler(handler: (baseUrl: string) => void | Promise<void>): () => void {
  resumeSyncHandlers.add(handler)

  return () => {
    resumeSyncHandlers.delete(handler)
  }
}

export function resumeDeferredSync(baseUrl: string): void {
  for (const handler of Array.from(resumeSyncHandlers)) {
    try {
      void handler(baseUrl)
    } catch (error) {
      console.warn(`[auth-terminal-state] resumeSyncHandler failed for ${baseUrl}:`, error)
    }
  }
}

export function resetAuthTerminalState(): void {
  $terminalSignedOutUrls.set([])
  resumeSyncHandlers.clear()
}

export function initAuthTerminalStateListener(): () => void {
  const desktop = (typeof window !== 'undefined' ? (window as any).hermesDesktop : undefined)

  if (!desktop?.auth?.onTerminalStateChanged) {
    return () => {}
  }

  return desktop.auth.onTerminalStateChanged((payload: { baseUrl: string; signedOut: boolean; reason?: string }) => {
    if (payload && payload.baseUrl) {
      setTerminalSignedOut(payload.baseUrl, payload.signedOut, payload.reason)
    }
  })
}

// Auto-initialize when running in browser / electron window
if (typeof window !== 'undefined') {
  initAuthTerminalStateListener()
}
