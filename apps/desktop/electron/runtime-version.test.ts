import assert from 'node:assert/strict'
import { describe, test } from 'vitest'

import {
  formatClientVersion,
  loadInstallStamp,
  resolveHermesVersionLadder,
  validateInstallStamp,
  validateStampBuildNumber,
  validateStampShortCommit,
  validateStampVersion
} from './runtime-version'

describe('Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2)', () => {
  describe('4.2(a): loadInstallStamp schema retention and validation', () => {
    test('retains valid version, shortCommit, and buildNumber', () => {
      const validStamp = {
        schemaVersion: 1,
        version: '0.21.0',
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: '28e38b39',
        buildNumber: 27745,
        branch: 'feat/desktop-client-installer-and-versioning',
        builtAt: '2026-09-04T10:00:00.000Z',
        dirty: false,
        source: 'local'
      }

      const validated = validateInstallStamp(validStamp, '/test/path/install-stamp.json')
      assert.ok(validated)
      assert.equal(validated.schemaVersion, 1)
      assert.equal(validated.version, '0.21.0')
      assert.equal(validated.commit, '28e38b39efea6c68bf839c7c4531c92401600c77')
      assert.equal(validated.shortCommit, '28e38b39')
      assert.equal(validated.buildNumber, 27745)
      assert.equal(validated.branch, 'feat/desktop-client-installer-and-versioning')
      assert.equal(validated.dirty, false)
      assert.equal(validated.source, 'local')
      assert.equal(validated.path, '/test/path/install-stamp.json')
    })
  })

  describe('4.2(b): Strict boolean dirty checking', () => {
    test('strictly preserves boolean true and false, rejecting string truthy/falsy values', () => {
      const stampTrue = {
        schemaVersion: 1,
        commit: '1234567890abcdef',
        dirty: true
      }
      assert.equal(validateInstallStamp(stampTrue, 'path')?.dirty, true)

      const stampFalse = {
        schemaVersion: 1,
        commit: '1234567890abcdef',
        dirty: false
      }
      assert.equal(validateInstallStamp(stampFalse, 'path')?.dirty, false)

      // String "true" should evaluate to false (strict typeof check)
      const stampStringTrue = {
        schemaVersion: 1,
        commit: '1234567890abcdef',
        dirty: 'true'
      }
      assert.equal(validateInstallStamp(stampStringTrue, 'path')?.dirty, false)

      // String "false" should evaluate to false
      const stampStringFalse = {
        schemaVersion: 1,
        commit: '1234567890abcdef',
        dirty: 'false'
      }
      assert.equal(validateInstallStamp(stampStringFalse, 'path')?.dirty, false)

      // Numbers or objects evaluate to false
      const stampNumber = {
        schemaVersion: 1,
        commit: '1234567890abcdef',
        dirty: 1
      }
      assert.equal(validateInstallStamp(stampNumber, 'path')?.dirty, false)
    })
  })

  describe('4.2(c): Malformed fields, schemaVersion mismatch, and non-hex short SHA', () => {
    test('rejects schemaVersion !== 1', () => {
      const stampV2 = {
        schemaVersion: 2,
        commit: '1234567890abcdef',
        version: '0.21.0'
      }
      assert.equal(validateInstallStamp(stampV2, 'path'), null)

      const stampV0 = {
        schemaVersion: 0,
        commit: '1234567890abcdef'
      }
      assert.equal(validateInstallStamp(stampV0, 'path'), null)
    })

    test('rejects non-hex or invalid length short SHA', () => {
      // 7 hex chars (too short)
      assert.equal(validateStampShortCommit('1234567'), null)
      // 9 hex chars (too long)
      assert.equal(validateStampShortCommit('123456789'), null)
      // 8 non-hex chars (e.g. contains 'z')
      assert.equal(validateStampShortCommit('1234567z'), null)
      // Valid 8 hex chars
      assert.equal(validateStampShortCommit('28e38b39'), '28e38b39')
      assert.equal(validateStampShortCommit('ABCDEF01'), 'ABCDEF01')
    })

    test('gracefully handles invalid JSON in loadInstallStamp', () => {
      const mockFs = {
        readFileSync: () => 'NOT_VALID_JSON{{{'
      }
      const stamp = loadInstallStamp(['/fake/install-stamp.json'], mockFs as any)
      assert.equal(stamp, null)
    })
  })

  describe('W-3: Explicit test branches for negative/fractional buildNumber, unreadable stamp, clean/dirty output', () => {
    test('negative buildNumber is rejected (normalized to null)', () => {
      assert.equal(validateStampBuildNumber(-1), null)
      assert.equal(validateStampBuildNumber(-100), null)

      const stampNegative = {
        schemaVersion: 1,
        commit: '1234567890abcdef',
        version: '0.21.0',
        buildNumber: -42
      }
      assert.equal(validateInstallStamp(stampNegative, 'path')?.buildNumber, null)
    })

    test('fractional buildNumber is rejected (normalized to null)', () => {
      assert.equal(validateStampBuildNumber(12.34), null)
      assert.equal(validateStampBuildNumber(0.5), null)

      const stampFractional = {
        schemaVersion: 1,
        commit: '1234567890abcdef',
        version: '0.21.0',
        buildNumber: 100.75
      }
      assert.equal(validateInstallStamp(stampFractional, 'path')?.buildNumber, null)
    })

    test('unreadable existing stamp throws I/O error and falls back gracefully', () => {
      const mockFs = {
        readFileSync: (p: string) => {
          if (p === '/unreadable/install-stamp.json') {
            throw new Error('EACCES: permission denied')
          }
          return JSON.stringify({
            schemaVersion: 1,
            commit: '1234567890abcdef',
            shortCommit: '12345678',
            version: '0.21.0'
          })
        }
      }

      // First path unreadable, second path valid -> falls back to candidate 2
      const stamp = loadInstallStamp(
        ['/unreadable/install-stamp.json', '/valid/install-stamp.json'],
        mockFs as any
      )
      assert.ok(stamp)
      assert.equal(stamp.version, '0.21.0')

      // All paths unreadable -> returns null
      const failedStamp = loadInstallStamp(
        ['/unreadable/install-stamp.json'],
        mockFs as any
      )
      assert.equal(failedStamp, null)
    })

    test('clean formatted output: "${version} (${shortCommit})"', () => {
      const cleanStamp = {
        schemaVersion: 1,
        version: '0.21.0',
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: '28e38b39',
        buildNumber: 27745,
        branch: 'main',
        builtAt: null,
        dirty: false,
        source: 'local' as const,
        path: '/path'
      }
      assert.equal(formatClientVersion(cleanStamp), '0.21.0 (28e38b39)')
    })

    test('dirty formatted output: "${version} (${shortCommit}) [DIRTY]"', () => {
      const dirtyStamp = {
        schemaVersion: 1,
        version: '0.21.0',
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: '28e38b39',
        buildNumber: 27745,
        branch: 'main',
        builtAt: null,
        dirty: true,
        source: 'local' as const,
        path: '/path'
      }
      assert.equal(formatClientVersion(dirtyStamp), '0.21.0 (28e38b39) [DIRTY]')
    })
  })

  describe('4.2(d) & 4.2(e): Packaged seam resolution and fallback ladder', () => {
    test('Rung 1: Dev/Source resolution prefers hermes_cli/__init__.py', () => {
      const mockFs = {
        existsSync: () => true,
        readFileSync: () => '__version__ = "0.21.0.dev1"\n'
      }
      const stamp = {
        schemaVersion: 1,
        version: '0.21.0',
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: '28e38b39',
        buildNumber: 27745,
        branch: 'main',
        builtAt: null,
        dirty: false,
        source: 'local' as const,
        path: '/path'
      }

      const version = resolveHermesVersionLadder({
        updateRoot: '/repo/root',
        installStamp: stamp,
        appVersion: '0.17.0',
        fsModule: mockFs as any
      })
      assert.equal(version, '0.21.0.dev1')
    })

    test('Rung 2: Packaged client-only runtime resolves from installStamp', () => {
      const stamp = {
        schemaVersion: 1,
        version: '0.21.0',
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: '28e38b39',
        buildNumber: 27745,
        branch: 'main',
        builtAt: null,
        dirty: false,
        source: 'local' as const,
        path: '/path'
      }

      // No local python source tree available
      const mockFs = {
        existsSync: () => false,
        readFileSync: () => { throw new Error('File not found') }
      }

      const version = resolveHermesVersionLadder({
        updateRoot: '/packaged/resources',
        installStamp: stamp,
        appVersion: '0.17.0',
        fsModule: mockFs as any
      })
      assert.equal(version, '0.21.0 (28e38b39)')
    })

    test('Rung 2: Packaged client-only runtime with dirty stamp appends [DIRTY]', () => {
      const dirtyStamp = {
        schemaVersion: 1,
        version: '0.21.0',
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: '28e38b39',
        buildNumber: 27745,
        branch: 'main',
        builtAt: null,
        dirty: true,
        source: 'local' as const,
        path: '/path'
      }

      const version = resolveHermesVersionLadder({
        updateRoot: null,
        installStamp: dirtyStamp,
        appVersion: '0.17.0'
      })
      assert.equal(version, '0.21.0 (28e38b39) [DIRTY]')
    })

    test('Rung 3: Ultimate fallback to app.getVersion() when stamp is missing or invalid', () => {
      // 1. Missing stamp
      const vMissing = resolveHermesVersionLadder({
        updateRoot: null,
        installStamp: null,
        appVersion: '0.17.0'
      })
      assert.equal(vMissing, '0.17.0')

      // 2. Stamp with missing/invalid version
      const invalidVersionStamp = {
        schemaVersion: 1,
        version: null,
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: '28e38b39',
        buildNumber: 27745,
        branch: 'main',
        builtAt: null,
        dirty: false,
        source: 'local' as const,
        path: '/path'
      }
      const vInvalidVersion = resolveHermesVersionLadder({
        updateRoot: null,
        installStamp: invalidVersionStamp,
        appVersion: '0.17.0'
      })
      assert.equal(vInvalidVersion, '0.17.0')

      // 3. Stamp with missing/invalid shortCommit
      const invalidShaStamp = {
        schemaVersion: 1,
        version: '0.21.0',
        commit: '28e38b39efea6c68bf839c7c4531c92401600c77',
        shortCommit: null,
        buildNumber: 27745,
        branch: 'main',
        builtAt: null,
        dirty: false,
        source: 'local' as const,
        path: '/path'
      }
      const vInvalidSha = resolveHermesVersionLadder({
        updateRoot: null,
        installStamp: invalidShaStamp,
        appVersion: '0.17.0'
      })
      assert.equal(vInvalidSha, '0.17.0')
    })
  })
})
