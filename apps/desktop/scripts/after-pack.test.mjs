import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { Platform } from 'app-builder-lib'
import { PlatformPackager } from 'app-builder-lib/out/platformPackager.js'
import { describe, expect, it, test, vi } from 'vitest'

import * as setExeIdentityModule from './set-exe-identity.mjs'
import afterPack from './after-pack.mjs'
import pkg from '../package.json' with { type: 'json' }

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
})

async function configuredHook(context) {
  if (pkg.build.afterPack) {
    const hook = await import(new URL(`../${pkg.build.afterPack}`, import.meta.url).href)
    await hook.default(context)
  }
}

function context(appOutDir, productFilename = 'Hermes Preview') {
  const packager = Object.assign(Object.create(PlatformPackager.prototype), {
    platform: Platform.MAC,
    appInfo: { productFilename },
    info: { framework: { distMacOsAppName: 'Electron.app' } }
  })
  return { appOutDir, electronPlatformName: 'darwin', packager }
}

it('restores app localizations from the filtered framework without copying locale data', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'hermes-locale-pack-'))
  try {
    const ctx = context(root)
    const framework = ctx.packager.getMacOsElectronFrameworkResourcesDir(root)
    const resources = ctx.packager.getResourcesDir(root)
    await mkdir(resources, { recursive: true })
    for (const name of ['nb.lproj', 'en_GB.lproj', 'nb_FEMININE.lproj']) {
      await mkdir(path.join(framework, name), { recursive: true })
      await writeFile(path.join(framework, name, 'locale.pak'), 'untouched locale data')
    }
    await writeFile(path.join(framework, 'not-a-directory.lproj'), 'not a locale')
    await mkdir(path.join(framework, 'other'), { recursive: true })
    await configuredHook(ctx)
    await configuredHook(ctx)
    expect((await readdir(resources)).sort()).toEqual(['en_GB.lproj', 'nb.lproj'])
    expect(await readdir(path.join(resources, 'nb.lproj'))).toEqual([])
    expect(await readFile(path.join(framework, 'nb.lproj', 'locale.pak'), 'utf8')).toBe('untouched locale data')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
})
