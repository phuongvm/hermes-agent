import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $pluginDecisions,
  $pluginRecords,
  publishPlugin,
  restoreBundledPlugin,
  setPluginEnabled,
  suspendBundledPlugin,
  suspendedBundledDefault
} from './plugins-store'

describe('plugins-store bundled suspend/restore lifecycle', () => {
  beforeEach(() => {
    $pluginRecords.set({})
    $pluginDecisions.set({})
  })

  it('suspends a bundled plugin by invoking deactivate, removing active record, and restoring cleanly', async () => {
    const activate = vi.fn(() => {
      publishPlugin({
        id: 'crew',
        kind: 'bundled',
        name: 'Crew',
        packageName: 'crew',
        status: 'loaded'
      })
    })

    const deactivate = vi.fn()

    publishPlugin(
      {
        id: 'crew',
        kind: 'bundled',
        name: 'Crew',
        packageName: 'crew',
        status: 'loaded'
      },
      { activate, deactivate },
      { defaultEnabled: true }
    )

    const suspended = suspendBundledPlugin('crew')

    expect(suspended).toEqual({ defaultEnabled: true })
    expect(suspendedBundledDefault('crew')).toBe(true)
    expect(deactivate).toHaveBeenCalledTimes(1)
    expect($pluginRecords.get().crew).toBeUndefined()

    const restored = restoreBundledPlugin('crew')

    expect(restored).toBe(true)
    expect(suspendedBundledDefault('crew')).toBeUndefined()
    expect(activate).toHaveBeenCalledTimes(1)
    expect($pluginRecords.get().crew).toMatchObject({
      id: 'crew',
      kind: 'bundled',
      packageName: 'crew',
      status: 'loaded'
    })

    // Restoring a second time when nothing is suspended is a safe false no-op.
    expect(restoreBundledPlugin('crew')).toBe(false)
  })

  it('tolerates a throwing bundled disposer during suspend and still restores cleanly', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    const activate = vi.fn(() => {
      publishPlugin({
        id: 'radar',
        kind: 'bundled',
        name: 'Radar',
        packageName: 'radar',
        status: 'loaded'
      })
    })

    const deactivate = vi.fn(() => {
      throw new Error('bundled dispose boom')
    })

    publishPlugin(
      {
        id: 'radar',
        kind: 'bundled',
        name: 'Radar',
        packageName: 'radar',
        status: 'loaded'
      },
      { activate, deactivate },
      { defaultEnabled: true }
    )

    expect(() => suspendBundledPlugin('radar')).not.toThrow()
    expect($pluginRecords.get().radar).toBeUndefined()
    expect(restoreBundledPlugin('radar')).toBe(true)
    expect(activate).toHaveBeenCalledTimes(1)
    expect($pluginRecords.get().radar?.status).toBe('loaded')

    consoleError.mockRestore()
  })

  it('respects explicit persisted false decisions and opt-in defaultEnabled:false on restore while keeping handles live', async () => {
    const activateCrew = vi.fn(() => {
      publishPlugin({
        id: 'crew',
        kind: 'bundled',
        name: 'Crew',
        packageName: 'crew',
        status: 'loaded'
      })
    })

    const deactivateCrew = vi.fn()

    publishPlugin(
      {
        id: 'crew',
        kind: 'bundled',
        name: 'Crew',
        packageName: 'crew',
        status: 'loaded'
      },
      { activate: activateCrew, deactivate: deactivateCrew },
      { defaultEnabled: true }
    )

    expect(suspendBundledPlugin('crew')).toEqual({ defaultEnabled: true })

    // User explicitly disabled 'crew' while overridden (or prior to restore).
    $pluginDecisions.set({ crew: false })

    expect(restoreBundledPlugin('crew')).toBe(true)
    expect(activateCrew).not.toHaveBeenCalled()
    expect($pluginRecords.get().crew).toMatchObject({
      id: 'crew',
      kind: 'bundled',
      packageName: 'crew',
      status: 'disabled'
    })

    // Toggling back on uses the restored bundled handle.
    await setPluginEnabled('crew', true)
    expect(activateCrew).toHaveBeenCalledTimes(1)
    expect($pluginRecords.get().crew?.status).toBe('loaded')

    // Non-bundled plugins cannot be suspended through suspendBundledPlugin.
    publishPlugin({ id: 'disk-only', kind: 'disk', name: 'Disk Only', status: 'loaded' })
    expect(suspendBundledPlugin('disk-only')).toBeNull()
    expect($pluginRecords.get()['disk-only']).toMatchObject({ kind: 'disk', status: 'loaded' })
  })
})
