/**
 * Runtime version resolution and install stamp validation for Hermes Desktop.
 * Provides the single authoritative runtime resolution ladder for:
 *   - hermes:version IPC handler
 *   - Native About panel
 *   - Renderer skew detection
 */

import fs from 'node:fs'
import path from 'node:path'

export const INSTALL_STAMP_SCHEMA_VERSION = 1

export interface InstallStamp {
  schemaVersion: number
  version: string | null
  commit: string
  shortCommit: string | null
  buildNumber: number | null
  branch: string | null
  builtAt: string | null
  dirty: boolean
  source: 'ci' | 'local' | 'fallback' | null
  path: string
}

export function validateStampVersion(version: unknown): string | null {
  if (typeof version !== 'string' || !version.trim()) {
    return null
  }

  const trimmed = version.trim()
  const match = trimmed.match(/^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$/)

  if (!match) {
    return null
  }

  const major = parseInt(match[1], 10)
  const minor = parseInt(match[2], 10)
  const patch = parseInt(match[3], 10)

  if (major < 0 || major > 65535 || minor < 0 || minor > 65535 || patch < 0 || patch > 65535) {
    return null
  }

  return trimmed
}

export function validateStampShortCommit(shortCommit: unknown): string | null {
  if (typeof shortCommit === 'string' && /^[0-9a-fA-F]{8}$/.test(shortCommit)) {
    return shortCommit
  }

  return null
}

export function validateStampBuildNumber(buildNumber: unknown): number | null {
  if (typeof buildNumber === 'number' && Number.isInteger(buildNumber) && buildNumber >= 0) {
    return buildNumber
  }

  return null
}

/**
 * Validate and normalize a parsed JSON object into an InstallStamp.
 * Returns null if schemaVersion != 1 or commit is invalid.
 */
export function validateInstallStamp(parsed: any, filePath: string): InstallStamp | null {
  if (!parsed || typeof parsed !== 'object') {
    return null
  }

  if (typeof parsed.commit !== 'string' || parsed.commit.length < 7) {
    return null
  }

  if (parsed.schemaVersion !== INSTALL_STAMP_SCHEMA_VERSION) {
    console.warn(
      `[hermes] install-stamp.json schemaVersion ${parsed.schemaVersion} != expected ${INSTALL_STAMP_SCHEMA_VERSION}; ignoring`
    )

    return null
  }

  // If shortCommit is present but malformed (non-hex or wrong length), reject the shortCommit.
  const shortCommit = validateStampShortCommit(parsed.shortCommit)
  const version = validateStampVersion(parsed.version)
  const buildNumber = validateStampBuildNumber(parsed.buildNumber)

  // Strict boolean check: reject truthy or falsy strings like "true" or "false"
  const dirty = typeof parsed.dirty === 'boolean' ? parsed.dirty : false

  return Object.freeze({
    schemaVersion: parsed.schemaVersion,
    version,
    commit: parsed.commit,
    shortCommit,
    buildNumber,
    branch: typeof parsed.branch === 'string' ? parsed.branch : null,
    builtAt: typeof parsed.builtAt === 'string' ? parsed.builtAt : null,
    dirty,
    source: parsed.source === 'ci' || parsed.source === 'local' || parsed.source === 'fallback' ? parsed.source : null,
    path: filePath
  })
}

/**
 * Load and validate install-stamp.json across candidate paths.
 */
export function loadInstallStamp(
  candidates?: string[] | null,
  fsModule: { readFileSync: (path: string, encoding: 'utf8') => string } = fs
): InstallStamp | null {
  const searchPaths =
    candidates ||
    [
      process.resourcesPath ? path.join(process.resourcesPath, 'install-stamp.json') : null,
      path.join(process.cwd(), 'build', 'install-stamp.json')
    ].filter((p): p is string => Boolean(p))

  for (const p of searchPaths) {
    try {
      const raw = fsModule.readFileSync(p, 'utf8')
      const parsed = JSON.parse(raw)
      const validated = validateInstallStamp(parsed, p)

      if (validated) {
        return validated
      }
    } catch (e: any) {
      console.warn(`[hermes] install-stamp.json found at ${p}, but parsing failed with ${e.message || e}`)
    }
  }

  return null
}

/**
 * Formats a client-only runtime version string from a valid install stamp.
 * Format: `${version} (${shortCommit})${dirty ? ' [DIRTY]' : ''}`
 */
export function formatClientVersion(stamp: InstallStamp): string {
  if (!stamp || !stamp.version) {
    return ''
  }

  const dirtySuffix = stamp.dirty ? ' [DIRTY]' : ''

  if (stamp.shortCommit) {
    return `${stamp.version} (${stamp.shortCommit})${dirtySuffix}`
  }

  return `${stamp.version}${dirtySuffix}`
}

export interface VersionResolutionContext {
  updateRoot?: string | null
  installStamp?: InstallStamp | null
  appVersion?: string | null
  fsModule?: {
    readFileSync: (p: string, enc: 'utf8') => string
    existsSync?: (p: string) => boolean
  }
}

/**
 * 3-Rung Version Resolution Ladder:
 *   Rung 1 (Dev / Source): Read hermes_cli/__init__.py if available.
 *   Rung 2 (Packaged Client-Only): Read bundled install stamp. Requires valid version & shortCommit.
 *   Rung 3 (Fallback): app.getVersion().
 */
export function resolveHermesVersionLadder(ctx: VersionResolutionContext = {}): string {
  const fileSystem = ctx.fsModule || fs

  // Rung 1: Source tree version
  if (ctx.updateRoot) {
    try {
      const initPath = path.join(ctx.updateRoot, 'hermes_cli', '__init__.py')
      const exists = fileSystem.existsSync ? fileSystem.existsSync(initPath) : true

      if (exists) {
        const raw = fileSystem.readFileSync(initPath, 'utf8')
        const match = raw.match(/__version__\s*=\s*["']([^"']+)["']/)

        if (match && match[1]) {
          return match[1]
        }
      }
    } catch {
      // Fall through to next rung
    }
  }

  // Rung 2: Packaged client-only runtime (requires both valid version and valid shortCommit)
  const stamp = ctx.installStamp

  if (stamp && stamp.version && stamp.shortCommit) {
    return formatClientVersion(stamp)
  }

  // Rung 3: App version fallback
  if (ctx.appVersion) {
    return ctx.appVersion
  }

  return '0.0.0'
}
