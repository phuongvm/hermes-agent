import assert from 'node:assert/strict'
import { describe, test, vi } from 'vitest'

import * as setExeIdentityModule from './set-exe-identity.mjs'
import afterPack from './after-pack.mjs'

describe('after-pack hook fail-closed packaging behavior (C-1R2)', () => {
  test('fails closed (re-throws) when stampExeIdentity rejects', async () => {
    const spy = vi.spyOn(setExeIdentityModule, 'stampExeIdentity').mockRejectedValue(
      new Error('rcedit binary exited with code 1: invalid PE header')
    )

    const fakeContext = {
      electronPlatformName: 'win32',
      appOutDir: '/fake/dist/win-unpacked',
      packager: {
        appInfo: {
          productFilename: 'Hermes'
        }
      }
    }

    await assert.rejects(
      async () => {
        await afterPack(fakeContext)
      },
      (err) => {
        assert.match(err.message, /rcedit binary exited with code 1/)
        return true
      }
    )

    assert.equal(spy.mock.calls.length, 1)
    spy.mockRestore()
  })

  test('succeeds when stampExeIdentity resolves', async () => {
    const spy = vi.spyOn(setExeIdentityModule, 'stampExeIdentity').mockResolvedValue(undefined)

    const fakeContext = {
      electronPlatformName: 'win32',
      appOutDir: '/fake/dist/win-unpacked',
      packager: {
        appInfo: {
          productFilename: 'Hermes'
        }
      }
    }

    await afterPack(fakeContext)
    assert.equal(spy.mock.calls.length, 1)
    spy.mockRestore()
  })

  test('returns early without stamping on non-win32 platforms', async () => {
    const spy = vi.spyOn(setExeIdentityModule, 'stampExeIdentity').mockRejectedValue(
      new Error('Should not be called on non-win32')
    )

    const macContext = {
      electronPlatformName: 'darwin',
      appOutDir: '/fake/dist/mac',
      packager: {
        appInfo: {
          productFilename: 'Hermes'
        }
      }
    }

    await afterPack(macContext)
    assert.equal(spy.mock.calls.length, 0)

    const linuxContext = {
      electronPlatformName: 'linux',
      appOutDir: '/fake/dist/linux',
      packager: {
        appInfo: {
          productFilename: 'Hermes'
        }
      }
    }

    await afterPack(linuxContext)
    assert.equal(spy.mock.calls.length, 0)
    spy.mockRestore()
  })
})
