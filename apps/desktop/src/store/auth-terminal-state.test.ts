import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'

import {
  initAuthTerminalStateListener,
  isTerminalSignedOut,
  normalizeBaseUrl,
  registerResumeSyncHandler,
  resetAuthTerminalState,
  setTerminalSignedOut
} from './auth-terminal-state'

describe('auth-terminal-state', () => {
  beforeEach(() => {
    resetAuthTerminalState()
    $connection.set(null)
  })

  afterEach(() => {
    resetAuthTerminalState()
    $connection.set(null)
    vi.restoreAllMocks()
  })

  it('normalizes base URLs correctly', () => {
    expect(normalizeBaseUrl('https://gateway.example.com/')).toBe('https://gateway.example.com')
    expect(normalizeBaseUrl('https://gateway.example.com///')).toBe('https://gateway.example.com')
    expect(normalizeBaseUrl('https://GATEWAY.Example.COM/api/')).toBe('https://gateway.example.com/api')
    expect(normalizeBaseUrl('   https://gateway.example.com   ')).toBe('https://gateway.example.com')
  })

  it('returns false initially when no URL is signed out', () => {
    expect(isTerminalSignedOut('https://gateway.example.com')).toBe(false)
  })

  it('marks normalized base URL as signed-out and isolates per base URL', () => {
    setTerminalSignedOut('https://gateway-a.example.com/', true)

    expect(isTerminalSignedOut('https://gateway-a.example.com')).toBe(true)
    expect(isTerminalSignedOut('https://gateway-a.example.com/')).toBe(true)
    expect(isTerminalSignedOut('https://gateway-b.example.com')).toBe(false)
  })

  it('checks active connection baseUrl when no URL is explicitly passed', () => {
    $connection.set({
      baseUrl: 'https://active-gateway.example.com',
      isFullscreen: false,
      nativeOverlayWidth: 0,
      token: 'tok',
      wsUrl: 'ws://active-gateway.example.com/ws',
      logs: []
    } as never)

    expect(isTerminalSignedOut()).toBe(false)

    setTerminalSignedOut('https://active-gateway.example.com', true)
    expect(isTerminalSignedOut()).toBe(true)
  })

  it('coalesces and executes registered resume sync handlers once upon confirmed sign-in', () => {
    const handler1 = vi.fn()
    const handler2 = vi.fn()

    const unreg1 = registerResumeSyncHandler(handler1)
    const unreg2 = registerResumeSyncHandler(handler2)

    setTerminalSignedOut('https://gateway.example.com', true)
    expect(handler1).not.toHaveBeenCalled()
    expect(handler2).not.toHaveBeenCalled()

    // Transitioning to false triggers resume
    setTerminalSignedOut('https://gateway.example.com', false)

    expect(isTerminalSignedOut('https://gateway.example.com')).toBe(false)
    expect(handler1).toHaveBeenCalledTimes(1)
    expect(handler1).toHaveBeenCalledWith('https://gateway.example.com')
    expect(handler2).toHaveBeenCalledTimes(1)
    expect(handler2).toHaveBeenCalledWith('https://gateway.example.com')

    // Subsequent clear on already signed-in does not re-trigger resume
    setTerminalSignedOut('https://gateway.example.com', false)
    expect(handler1).toHaveBeenCalledTimes(1)

    unreg1()
    unreg2()
  })

  it('subscribes to window.hermesDesktop.auth.onTerminalStateChanged when initialized', () => {
    let listener: ((payload: { baseUrl: string; signedOut: boolean; reason?: string }) => void) | null = null

    const unsubscribe = vi.fn()

    ;(window as any).hermesDesktop = {
      auth: {
        onTerminalStateChanged: vi.fn(cb => {
          listener = cb

          return unsubscribe
        })
      }
    }

    const cleanup = initAuthTerminalStateListener()

    expect(window.hermesDesktop.auth?.onTerminalStateChanged).toHaveBeenCalledTimes(1)
    expect(listener).not.toBeNull()

    listener!({ baseUrl: 'https://ipc-gateway.example.com', signedOut: true, reason: 'terminal_401' })
    expect(isTerminalSignedOut('https://ipc-gateway.example.com')).toBe(true)

    listener!({ baseUrl: 'https://ipc-gateway.example.com', signedOut: false })
    expect(isTerminalSignedOut('https://ipc-gateway.example.com')).toBe(false)

    cleanup()
    expect(unsubscribe).toHaveBeenCalledTimes(1)
  })
})
