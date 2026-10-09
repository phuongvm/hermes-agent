// @vitest-environment jsdom
/// <reference types="vitest/globals" />
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'

import { SIDEBAR_NAV_AREA } from '@/app/routes'
import type { SidebarNavItem } from '@/app/types'
import { SidebarProvider } from '@/components/ui/sidebar'
import { createPluginContext } from '@/contrib/plugin'
import { registry } from '@/contrib/registry'
import { $newChatProfile } from '@/store/profile'
import { $routeTiles, closeRouteTile, openRouteTile } from '@/store/route-tiles'

import { SidebarNavigation, SidebarTools } from './navigation'

const icon = () => null
const settings: SidebarNavItem = { id: 'settings', label: 'Settings', icon, route: '/settings' }
const openspec: SidebarNavItem = { id: 'openspec', label: 'OpenSpec', icon, route: '/openspec' }
const crew: SidebarNavItem = { id: 'crew', label: 'Crew', icon, route: '/crew', asTile: true }
const newSession: SidebarNavItem = { id: 'new-session', label: 'New session', icon, action: 'new-session' }

const props = {
  currentView: 'chat' as const,
  navItems: [settings],
  newSessionKbd: [],
  newSessionKbdFlash: false,
  onNavigate: vi.fn(),
  onNewSessionSplit: vi.fn()
}

function RoutedTools({
  navItems = props.navItems,
  onNavigate
}: {
  navItems?: SidebarNavItem[]
  onNavigate?: (item: SidebarNavItem) => void
} = {}) {
  const { pathname } = useLocation()
  const navigate = useNavigate()

  return (
    <>
      <output aria-label="Current route">{pathname}</output>
      <SidebarTools
        {...props}
        navItems={navItems}
        onNavigate={
          onNavigate ??
          (item => {
            if (item.asTile && item.route) {
              openRouteTile(item.route, 'center')

              return
            }

            if (item.route) {
              navigate(item.route)
            }
          })
        }
      />
    </>
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  $newChatProfile.set(null)

  for (const tile of $routeTiles.get()) {
    closeRouteTile(tile.path)
  }
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

  it('renders Crew below OpenSpec under TOOLS and navigates to /crew on click', () => {
    const onNav = vi.fn()
    render(
      <MemoryRouter>
        <SidebarProvider>
          <SidebarTools {...props} navItems={[openspec, crew, settings]} onNavigate={onNav} />
        </SidebarProvider>
      </MemoryRouter>
    )

    const toggle = screen.getByRole('button', { name: 'Tools' })
    fireEvent.click(toggle)

    const buttons = screen.getAllByRole('button')
    const toolLabels = buttons.map(b => b.textContent?.trim()).filter(Boolean)

    const openspecIndex = toolLabels.findIndex(l => l?.includes('OpenSpec'))
    const crewIndex = toolLabels.findIndex(l => l?.includes('Crew'))

    expect(openspecIndex).toBeGreaterThan(-1)
    expect(crewIndex).toBeGreaterThan(openspecIndex)

    const crewButton = screen.getByRole('button', { name: 'Crew' })
    fireEvent.click(crewButton)
    expect(onNav).toHaveBeenCalledWith(crew)
  })

  it('triggers route tile opening in center dock when clicking a sidebar item with asTile: true without changing page route', () => {
    render(
      <MemoryRouter initialEntries={['/tile-one']}>
        <SidebarProvider>
          <RoutedTools navItems={[openspec, crew, settings]} />
        </SidebarProvider>
      </MemoryRouter>
    )

    const toggle = screen.getByRole('button', { name: 'Tools' })
    fireEvent.click(toggle)

    expect(screen.getByLabelText('Current route').textContent).toBe('/tile-one')
    expect($routeTiles.get()).toEqual([])

    const crewButton = screen.getByRole('button', { name: 'Crew' })
    fireEvent.click(crewButton)

    // Current page route did not navigate away
    expect(screen.getByLabelText('Current route').textContent).toBe('/tile-one')

    // Opened as a route tile docked in center
    expect($routeTiles.get()).toEqual([
      expect.objectContaining({ path: '/crew', dir: 'center' })
    ])

    // Clicking again is idempotent and maintains the tile
    fireEvent.click(crewButton)
    expect($routeTiles.get()).toHaveLength(1)
    expect($routeTiles.get()[0]).toMatchObject({ path: '/crew', dir: 'center' })
  })

  it('orders contributed navigation items by order property with OpenSpec (50) preceding Crew (55) registered out of sequence', () => {
    const disposers: (() => void)[] = []
    const crewCtx = createPluginContext('crew', d => disposers.push(d))
    const openspecCtx = createPluginContext('openspec', d => disposers.push(d))

    try {
      crewCtx.register({
        area: SIDEBAR_NAV_AREA,
        id: 'nav',
        order: 55,
        data: { label: 'Crew', path: '/crew', codicon: 'organization', asTile: true }
      })
      openspecCtx.register({
        area: SIDEBAR_NAV_AREA,
        id: 'nav',
        order: 50,
        data: { label: 'OpenSpec', path: '/openspec', codicon: 'list-tree' }
      })

      const areaItems = registry
        .getArea(SIDEBAR_NAV_AREA)
        .filter(c => c.id === 'openspec:nav' || c.id === 'crew:nav')

      expect(areaItems.map(c => c.id)).toEqual(['openspec:nav', 'crew:nav'])
      expect(areaItems[0].order).toBe(50)
      expect(areaItems[1].order).toBe(55)
      expect(areaItems[0].order).toBeLessThan(areaItems[1].order!)
      expect(areaItems[1].data).toMatchObject({ asTile: true })
    } finally {
      disposers.forEach(d => d())
    }
  })
})

describe('SidebarNavigation', () => {
  it('triggers route tile opening when clicking an asTile navigation item in SidebarNavigation', () => {
    const onNav = vi.fn(item => {
      if (item.asTile && item.route) {
        openRouteTile(item.route, 'center')
      }
    })

    render(
      <MemoryRouter>
        <SidebarProvider>
          <SidebarNavigation {...props} navItems={[crew, settings]} onNavigate={onNav} />
        </SidebarProvider>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Crew' }))
    expect(onNav).toHaveBeenCalledWith(crew)
    expect($routeTiles.get()).toEqual([
      expect.objectContaining({ path: '/crew', dir: 'center' })
    ])
  })

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
