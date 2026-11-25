import { afterEach, describe, expect, it, vi } from 'vitest'

import { $notifications } from '@/store/notifications'

import { discoverBundledPlugins } from './plugins'
import { $pluginDecisions, $pluginRecords, pluginActive, setPluginEnabled } from './plugins-store'
import { registry } from './registry'

vi.mock('./runtime-loader', () => ({ watchRuntimePlugins: vi.fn() }))

describe('discoverBundledPlugins (bundled org plugins)', () => {
  afterEach(async () => {
    for (const id of ['crew', 'openspec', 'radar', 'redline'] as const) {
      await setPluginEnabled(id, false)
    }

    $pluginDecisions.set({})
    $notifications.set([])
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('registers crew, openspec, radar, and redline at boot with scoped packageName and degrades cleanly on 404 backends while leaving upstream bundled plugins standalone', async () => {
    $pluginDecisions.set({ accent: false, 'hermes-bots': false, kanban: false })
    $notifications.set([])
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 }))
    )

    discoverBundledPlugins()

    const records = $pluginRecords.get()

    for (const id of ['crew', 'openspec', 'radar', 'redline'] as const) {
      expect(records[id]).toMatchObject({
        id,
        kind: 'bundled',
        packageName: id,
        status: 'loaded'
      })
    }

    for (const id of ['hermes-bots', 'kanban', 'radio'] as const) {
      expect(records[id]?.kind).toBe('bundled')
      expect(records[id]?.packageName).toBeUndefined()
    }

    // Absent backend halves (HTTP 404) must not emit error notification toasts.
    await Promise.resolve()
    expect($notifications.get()).toHaveLength(0)

    // Explicit user disable persists decision, deactivates contributions, and preserves bundled metadata.
    await setPluginEnabled('radar', false)
    expect($pluginDecisions.get().radar).toBe(false)
    expect(pluginActive('radar', true)).toBe(false)
    expect($pluginRecords.get().radar).toMatchObject({
      id: 'radar',
      kind: 'bundled',
      packageName: 'radar',
      status: 'disabled'
    })
    expect(registry.getArea('statusBar.right').some(item => item.source === 'plugin:radar')).toBe(false)
  })
})
