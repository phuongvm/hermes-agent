// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SidebarNavItem } from '@/app/types'
import { SidebarProvider } from '@/components/ui/sidebar'
import { $newChatProfile } from '@/store/profile'

import { SidebarNavigation, SidebarTools } from './navigation'

const icon = () => null
const settings: SidebarNavItem = { id: 'settings', label: 'Settings', icon, route: '/settings' }
const newSession: SidebarNavItem = { id: 'new-session', label: 'New session', icon, action: 'new-session' }

const props = {
  currentView: 'chat' as const,
  navItems: [settings],
  newSessionKbd: [],
  newSessionKbdFlash: false,
  onNavigate: vi.fn(),
  onNewSessionSplit: vi.fn()
}

function RoutedTools() {
  const { pathname } = useLocation()
  const navigate = useNavigate()

  return (
    <>
      <output aria-label="Current route">{pathname}</output>
      <SidebarTools {...props} onNavigate={item => navigate(item.route!)} />
    </>
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  $newChatProfile.set(null)
})

describe('SidebarTools', () => {
  it('links the disclosure to hidden content and resets to collapsed on a fresh mount', () => {
    const mount = () =>
      render(
        <MemoryRouter>
          <SidebarProvider>
            <SidebarTools {...props} />
          </SidebarProvider>
        </MemoryRouter>
      )

    const first = mount()
    const toggle = screen.getByRole('button', { name: 'Tools' })
    const content = toggle.ownerDocument.getElementById(toggle.getAttribute('aria-controls')!)!

    expect(content).not.toBeNull()
    expect(content.hidden).toBe(true)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull()

    fireEvent.click(toggle)
    expect(content.hidden).toBe(false)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(within(content).getByRole('button', { name: 'Settings' })).toBeTruthy()
    expect(props.onNavigate).not.toHaveBeenCalled()

    first.unmount()
    mount()
    expect(screen.getByRole('button', { name: 'Tools' }).getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull()
  })

  it('navigates only on a utility click and preserves the route through collapse and reopen', () => {
    render(
      <MemoryRouter initialEntries={['/tile-one']}>
        <SidebarProvider>
          <RoutedTools />
        </SidebarProvider>
      </MemoryRouter>
    )
    const toggle = screen.getByRole('button', { name: 'Tools' })
    fireEvent.click(toggle)
    expect(screen.getByLabelText('Current route').textContent).toBe('/tile-one')
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByLabelText('Current route').textContent).toBe('/settings')
    fireEvent.click(toggle)
    fireEvent.click(toggle)
    expect(screen.getByLabelText('Current route').textContent).toBe('/settings')
  })
})

describe('SidebarNavigation', () => {
  it('clears a stale new-chat profile only when New session is explicitly invoked', () => {
    $newChatProfile.set('work')
    render(
      <MemoryRouter>
        <SidebarProvider>
          <SidebarNavigation {...props} navItems={[newSession, settings]} />
        </SidebarProvider>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect($newChatProfile.get()).toBe('work')
    expect(props.onNavigate).toHaveBeenLastCalledWith(settings)
    fireEvent.click(screen.getByRole('button', { name: 'New session' }))
    expect($newChatProfile.get()).toBeNull()
    expect(props.onNavigate).toHaveBeenLastCalledWith(newSession)
    expect(props.onNewSessionSplit).not.toHaveBeenCalled()
  })
})
