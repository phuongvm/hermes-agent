import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import {
  bundledOrgPluginMeta,
  evaluateBundledCollision,
  resolveBundledPackageName,
  sha256Hex
} from './bundled-plugin-policy'

const __dirname = dirname(fileURLToPath(import.meta.url))

function nodeSha256(input: string): string {
  return createHash('sha256').update(input, 'utf8').digest('hex')
}

describe('bundled-plugin-policy', () => {
  describe('sha256Hex', () => {
    it('matches Node crypto SHA-256 across empty, ASCII, padding boundaries, multibyte UTF-8, and real plugin bundle', () => {
      const vectors = [
        '',
        'abc',
        'a'.repeat(55),
        'a'.repeat(56),
        'a'.repeat(63),
        'a'.repeat(64),
        'a'.repeat(65),
        'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq',
        'Xin chào 🚀 Hermes Desktop — 4 org plugins (crew · openspec · radar · redline) 𝄞'
      ]

      for (const input of vectors) {
        expect(sha256Hex(input)).toBe(nodeSha256(input))
      }

      expect(sha256Hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
      expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

      const crewBundle = readFileSync(resolve(__dirname, '../plugins/crew/plugin.js'), 'utf8')
      const crewMeta = bundledOrgPluginMeta('crew')

      expect(crewMeta).toBeDefined()
      expect(sha256Hex(crewBundle)).toBe(nodeSha256(crewBundle))
      expect(sha256Hex(crewBundle)).toBe(crewMeta?.sha256)
    })
  })

  describe('resolveBundledPackageName & bundledOrgPluginMeta', () => {
    it('returns manifest packageName for the 4 org plugins and explicit plugin.packageName overrides', () => {
      for (const id of ['crew', 'openspec', 'radar', 'redline'] as const) {
        const meta = bundledOrgPluginMeta(id)

        expect(meta).toMatchObject({ id, packageName: id })
        expect(meta?.sha256).toMatch(/^[0-9a-f]{64}$/)
        expect(resolveBundledPackageName({ id })).toBe(id)
      }

      expect(resolveBundledPackageName({ id: 'custom-ui', packageName: 'hermes-custom-pkg' })).toBe('hermes-custom-pkg')
    })

    it('returns undefined for upstream standalone bundled plugins (hermes-bots, kanban, radio)', () => {
      for (const id of ['hermes-bots', 'kanban', 'radio'] as const) {
        expect(bundledOrgPluginMeta(id)).toBeUndefined()
        expect(resolveBundledPackageName({ id })).toBeUndefined()
      }
    })
  })

  describe('evaluateBundledCollision (3-way policy)', () => {
    const baseSource = 'export default { id: "crew", register() {} }\n'

    const baseMeta = {
      id: 'crew',
      packageName: 'crew',
      sha256: nodeSha256(baseSource),
      sourceHint: 'crew/desktop/plugin.js',
      syncedAtMs: 1_760_000_000_000
    }

    it('returns override (linked-dev) when linkedSource is set, even if bytes match bundled', () => {
      const decision = evaluateBundledCollision(
        { id: 'crew', packageName: 'crew' },
        {
          id: 'crew',
          linkedSource: '/workspace/oss/crew/desktop',
          packageName: 'crew',
          source: baseSource,
          sourceMtimeMs: baseMeta.syncedAtMs - 5_000
        },
        baseMeta
      )

      expect(decision).toEqual({ action: 'override', reason: 'linked-dev' })
    })

    it('returns override (newer-unified) when non-linked unified package has different sha256 AND newer sourceMtimeMs', () => {
      const decision = evaluateBundledCollision(
        { id: 'crew', packageName: 'crew' },
        {
          id: 'crew',
          packageName: 'crew',
          source: 'export default { id: "crew", name: "Crew vNext", register() {} }\n',
          sourceMtimeMs: baseMeta.syncedAtMs + 1
        },
        baseMeta
      )

      expect(decision).toEqual({ action: 'override', reason: 'newer-unified' })
    })

    it('returns dedup-bundled (identical-unified) when non-linked unified package has matching sha256 regardless of mtime', () => {
      expect(
        evaluateBundledCollision(
          { id: 'crew', packageName: 'crew' },
          {
            id: 'crew',
            packageName: 'crew',
            source: baseSource,
            sourceMtimeMs: baseMeta.syncedAtMs
          },
          baseMeta
        )
      ).toEqual({ action: 'dedup-bundled', reason: 'identical-unified' })

      expect(
        evaluateBundledCollision(
          { id: 'crew', packageName: 'crew' },
          {
            id: 'crew',
            packageName: 'crew',
            source: baseSource.replace(/\n/g, '\r\n'),
            sourceMtimeMs: baseMeta.syncedAtMs + 999_999
          },
          baseMeta
        )
      ).toEqual({ action: 'dedup-bundled', reason: 'identical-unified' })
    })

    it('returns dedup-bundled (older-unified) when non-linked unified package has different sha256 but older/equal/missing mtime', () => {
      const olderSource = 'export default { id: "crew", name: "Crew old", register() {} }\n'

      expect(
        evaluateBundledCollision(
          { id: 'crew', packageName: 'crew' },
          {
            id: 'crew',
            packageName: 'crew',
            source: olderSource,
            sourceMtimeMs: baseMeta.syncedAtMs
          },
          baseMeta
        )
      ).toEqual({ action: 'dedup-bundled', reason: 'older-unified' })

      expect(
        evaluateBundledCollision(
          { id: 'crew', packageName: 'crew' },
          {
            id: 'crew',
            packageName: 'crew',
            source: olderSource
          },
          baseMeta
        )
      ).toEqual({ action: 'dedup-bundled', reason: 'older-unified' })
    })

    it('returns shadow-stale (standalone-disk) for standalone or unmatched disk copies', () => {
      expect(
        evaluateBundledCollision(
          { id: 'hermes-bots' },
          {
            id: 'hermes-bots',
            source: 'export default { id: "hermes-bots", register() {} }\n'
          }
        )
      ).toEqual({ action: 'shadow-stale', reason: 'standalone-disk' })

      expect(
        evaluateBundledCollision(
          { id: 'crew', packageName: 'crew' },
          {
            id: 'crew',
            source: 'export default { id: "crew", register() {} }\n'
          },
          baseMeta
        )
      ).toEqual({ action: 'shadow-stale', reason: 'standalone-disk' })
    })
  })
})
