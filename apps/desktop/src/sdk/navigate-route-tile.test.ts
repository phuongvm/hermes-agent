/**
 * `host.navigate` to a contributed route whose sidebar row declares
 * `asTile: true` (Crew's `/crew`) opens or fronts that route's CENTER tile —
 * the same thing a click on the row does (`selectSidebarItem` →
 * `openRouteTile(route, 'center')`) — instead of writing the hash, which would
 * swap the active chat out of the `workspace` pane. A plugin's in-app link
 * (Crew's chat ticket link `/crew?card=<id>&n=<stamp>`) lands on its tab and
 * the chat the owner was reading stays where it was.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $workspaceIsPage, ROUTES_AREA, sessionRoute, SIDEBAR_NAV_AREA } from '@/app/routes'
import { registry } from '@/contrib/registry'
import { host } from '@/sdk'
import { $routeTiles, closeRouteTile } from '@/store/route-tiles'

vi.mock('@/components/pane-shell/tree/store', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  noteActiveTreeGroup: vi.fn(),
  revealTreePane: vi.fn()
}))

const { revealTreePane } = await import('@/components/pane-shell/tree/store')

const CHAT = sessionRoute('sess-active')

function contribute(path: string, asTile: boolean | undefined): () => void {
  return registry.registerMany([
    { area: ROUTES_AREA, data: { path }, id: `page${path}`, render: () => null },
    { area: SIDEBAR_NAV_AREA, data: { asTile, codicon: 'organization', label: 'Crew', path }, id: `nav${path}` }
  ])
}

beforeEach(() => {
  vi.mocked(revealTreePane).mockClear()
  $workspaceIsPage.set(false)
  window.location.hash = `#${CHAT}`
})

afterEach(() => {
  for (const t of $routeTiles.get()) {
    closeRouteTile(t.path)
  }

  $workspaceIsPage.set(false)
})

describe('host.navigate to an asTile route', () => {
  it('opens the route as a center tile and leaves the active chat route alone', () => {
    const dispose = contribute('/crew', true)

    try {
      host.navigate('/crew?card=t_d8403bb8&n=1')

      expect($routeTiles.get()).toEqual([{ dir: 'center', path: '/crew' }])
      expect(revealTreePane).toHaveBeenCalledWith('route-tile:/crew')
      expect(window.location.hash).toBe(`#${CHAT}`)
      expect($workspaceIsPage.get()).toBe(false)
    } finally {
      dispose()
    }
  })

  it('fronts the tile it already has: one tile, revealed again', () => {
    const dispose = contribute('/crew', true)

    try {
      host.navigate('/crew?card=t_a&n=1')
      host.navigate('#/crew?card=t_b&n=2')

      expect($routeTiles.get()).toEqual([{ dir: 'center', path: '/crew' }])
      expect(vi.mocked(revealTreePane).mock.calls.filter(([p]) => p === 'route-tile:/crew')).toHaveLength(2)
      expect(window.location.hash).toBe(`#${CHAT}`)
    } finally {
      dispose()
    }
  })

  it('keeps hash navigation for a contributed route without asTile', () => {
    const dispose = contribute('/plain', undefined)

    try {
      host.navigate('/plain?x=1')

      expect($routeTiles.get()).toEqual([])
      expect(window.location.hash).toBe('#/plain?x=1')
    } finally {
      dispose()
    }
  })

  it('keeps hash navigation once the asTile row is gone (plugin disabled)', () => {
    contribute('/crew', true)()

    host.navigate('/crew?card=t_x&n=3')

    expect($routeTiles.get()).toEqual([])
    expect(window.location.hash).toBe('#/crew?card=t_x&n=3')
  })
})
