#!/usr/bin/env node
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DEFAULT_REPO_ROOT = resolve(__dirname, '..', '..')

export const ORG_PLUGINS = [
  { id: 'crew', ossRepo: 'crew', packageName: 'crew', sourceHint: 'crew/desktop/plugin.js' },
  { id: 'openspec', ossRepo: 'hermes-openspec', packageName: 'openspec', sourceHint: 'hermes-openspec/desktop/plugin.js' },
  { id: 'radar', ossRepo: 'radar', packageName: 'radar', sourceHint: 'radar/desktop/plugin.js' },
  { id: 'redline', ossRepo: 'hermes-redline', packageName: 'redline', sourceHint: 'hermes-redline/desktop/plugin.js' }
]

export const ALLOWED_IMPORT_SPECIFIERS = new Set([
  '@hermes/plugin-sdk',
  'react',
  'react/jsx-runtime',
  'react/jsx-dev-runtime'
])

const importSpecifierRe = () => /(from\s*|import\s*\(\s*|import\s+)(['"])([^'"]+)\2/g
const regexKeywordRe = /^(?:await|case|delete|do|else|in|instanceof|new|of|return|throw|typeof|void|yield)$/

function isRegexStart(source, slash) {
  let j = slash - 1

  while (j >= 0 && /\s/.test(source[j])) {
    j -= 1
  }

  if (j < 0) {
    return true
  }

  const prev = source[j]

  if (prev === '+' || prev === '-') {
    return source[j - 1] !== prev
  }

  if (prev === ')' || prev === ']' || prev === "'" || prev === '"' || prev === '`') {
    return false
  }

  if (prev === '}') {
    return true
  }

  if (/[A-Za-z0-9_$]/.test(prev)) {
    let k = j

    while (k >= 0 && /[A-Za-z0-9_$]/.test(source[k])) {
      k -= 1
    }

    if (source[k] === '.') {
      return false
    }

    return regexKeywordRe.test(source.slice(k + 1, j + 1))
  }

  return true
}

function regexEnd(source, slash) {
  let j = slash + 1
  let inClass = false

  while (j < source.length) {
    const c = source[j]

    if (c === '\\') {
      j += 2
    } else if (c === '\n') {
      return -1
    } else if (c === '[') {
      inClass = true
      j += 1
    } else if (c === ']') {
      inClass = false
      j += 1
    } else if (c === '/' && !inClass) {
      j += 1

      while (j < source.length && /[A-Za-z]/.test(source[j])) {
        j += 1
      }

      return j
    } else {
      j += 1
    }
  }

  return -1
}

function codeRanges(source) {
  const ranges = []
  const stack = []
  let state = 'code'
  let codeStart = 0
  let i = 0

  const closeCode = end => {
    if (end > codeStart) {
      ranges.push([codeStart, end])
    }
  }

  while (i < source.length) {
    const ch = source[i]
    const next = i + 1 < source.length ? source[i + 1] : ''

    if (state === 'code') {
      if (ch === '/' && next === '/') {
        closeCode(i)
        state = 'line-comment'
        i += 2
      } else if (ch === '/' && next === '*') {
        closeCode(i)
        state = 'block-comment'
        i += 2
      } else if (ch === "'") {
        closeCode(i)
        state = 'single'
        i += 1
      } else if (ch === '"') {
        closeCode(i)
        state = 'double'
        i += 1
      } else if (ch === '`') {
        closeCode(i)
        stack.push('template')
        state = 'template'
        i += 1
      } else if (ch === '/') {
        const end = isRegexStart(source, i) ? regexEnd(source, i) : -1

        if (end > 0) {
          closeCode(i)
          i = end
          codeStart = i
        } else {
          i += 1
        }
      } else if (ch === '}' && stack[stack.length - 1] === 'expr') {
        closeCode(i)
        stack.pop()
        state = 'template'
        i += 1
      } else {
        i += 1
      }

      continue
    }

    if (state === 'line-comment') {
      if (ch === '\n') {
        state = 'code'
        codeStart = i
      }

      i += 1
      continue
    }

    if (state === 'block-comment') {
      if (ch === '*' && next === '/') {
        i += 2
        state = 'code'
        codeStart = i
      } else {
        i += 1
      }

      continue
    }

    if (state === 'single' || state === 'double') {
      if (ch === '\\') {
        i += 2
      } else if (ch === (state === 'single' ? "'" : '"')) {
        i += 1
        state = 'code'
        codeStart = i
      } else if (ch === '\n') {
        i += 1
        state = 'code'
        codeStart = i
      } else {
        i += 1
      }

      continue
    }

    if (ch === '\\') {
      i += 2
    } else if (ch === '$' && next === '{') {
      stack.push('expr')
      state = 'code'
      i += 2
      codeStart = i
    } else if (ch === '`') {
      stack.pop()
      state = 'code'
      i += 1
      codeStart = i
    } else {
      i += 1
    }
  }

  closeCode(source.length)

  return ranges
}

function inCode(ranges, at) {
  for (const [start, end] of ranges) {
    if (at < start) {
      return false
    }

    if (at < end) {
      return true
    }
  }

  return false
}

export function findUnsupportedImports(source) {
  const unsupported = new Set()
  const ranges = codeRanges(source)

  for (const match of source.matchAll(importSpecifierRe())) {
    const spec = match[3]

    if (!spec || !inCode(ranges, match.index ?? 0)) {
      continue
    }

    if (!ALLOWED_IMPORT_SPECIFIERS.has(spec)) {
      unsupported.add(spec)
    }
  }

  return [...unsupported]
}

function sha256Buffer(buf) {
  return createHash('sha256').update(buf).digest('hex')
}

function isRegularNonEmptyFile(filePath) {
  try {
    const st = statSync(filePath)
    return st.isFile() && st.size > 0
  } catch {
    return false
  }
}

function resolvePluginSource(spec, { repoRoot, pluginsRoot, sourceMap }) {
  if (sourceMap && Object.hasOwn(sourceMap, spec.id)) {
    const explicit = resolve(sourceMap[spec.id])

    if (!isRegularNonEmptyFile(explicit)) {
      throw new Error(`[sync-desktop-plugins] ${spec.id}: --source-map path missing or empty: ${explicit}`)
    }

    return explicit
  }

  if (pluginsRoot) {
    const rootCandidate = resolve(pluginsRoot, spec.id, 'desktop', 'plugin.js')

    if (isRegularNonEmptyFile(rootCandidate)) {
      return rootCandidate
    }

    const ossCandidate = resolve(pluginsRoot, spec.ossRepo, 'desktop', 'plugin.js')

    if (isRegularNonEmptyFile(ossCandidate)) {
      return ossCandidate
    }

    throw new Error(`[sync-desktop-plugins] ${spec.id}: not found under --plugins-root (${rootCandidate})`)
  }

  const candidates = [resolve(repoRoot, '..', spec.ossRepo, 'desktop', 'plugin.js')]

  // When invoked from a dedicated worktree (`<workspace>/.worktrees/<slug>`), also probe `<workspace>/oss/<ossRepo>`.
  if (basename(resolve(repoRoot, '..')) === '.worktrees') {
    candidates.push(resolve(repoRoot, '..', '..', 'oss', spec.ossRepo, 'desktop', 'plugin.js'))
  }

  const hermesHome = process.env.HERMES_HOME ? resolve(process.env.HERMES_HOME) : join(homedir(), '.hermes')
  candidates.push(join(hermesHome, 'plugins', spec.id, 'desktop', 'plugin.js'))

  for (const candidate of candidates) {
    if (isRegularNonEmptyFile(candidate)) {
      return candidate
    }
  }

  throw new Error(
    `[sync-desktop-plugins] ${spec.id}: could not resolve source plugin.js (checked: ${candidates.join(', ')})`
  )
}

function readExistingManifestMap(manifestPath) {
  const map = new Map()

  if (!existsSync(manifestPath)) {
    return map
  }

  try {
    const parsed = JSON.parse(readFileSync(manifestPath, 'utf8'))

    if (parsed && Array.isArray(parsed.plugins)) {
      for (const entry of parsed.plugins) {
        if (entry && typeof entry.id === 'string') {
          map.set(entry.id, entry)
        }
      }
    }
  } catch {
    // Rebuilt below.
  }

  return map
}

export function syncDesktopPlugins({
  repoRoot = DEFAULT_REPO_ROOT,
  pluginsRoot = undefined,
  sourceMap = undefined,
  touch = false
} = {}) {
  const targetPluginsDir = resolve(repoRoot, 'apps', 'desktop', 'src', 'plugins')
  const manifestPath = join(targetPluginsDir, 'org-plugins.manifest.json')
  const prevById = readExistingManifestMap(manifestPath)

  const staged = ORG_PLUGINS.map(spec => {
    const sourcePath = resolvePluginSource(spec, { repoRoot, pluginsRoot, sourceMap })
    // Normalize CRLF -> LF so staged plugin.js files match `.gitattributes` (`*.js text eol=lf`)
    // identically in the working tree, Git index, and fresh checkouts across Windows/macOS/Linux.
    const normalizedText = readFileSync(sourcePath, 'utf8').replace(/\r\n/g, '\n')
    const bytes = Buffer.from(normalizedText, 'utf8')
    const unsupported = findUnsupportedImports(normalizedText)

    if (unsupported.length > 0) {
      throw new Error(
        `[sync-desktop-plugins] ${spec.id} (${sourcePath}) contains disallowed import specifier(s): ${unsupported.join(', ')}`
      )
    }

    const sha256 = sha256Buffer(bytes)
    const prev = prevById.get(spec.id)
    const syncedAtMs =
      !touch &&
      prev &&
      prev.sha256 === sha256 &&
      typeof prev.syncedAtMs === 'number' &&
      Number.isFinite(prev.syncedAtMs)
        ? prev.syncedAtMs
        : Math.round(statSync(sourcePath).mtimeMs)

    return { bytes, sha256, spec, syncedAtMs }
  })

  mkdirSync(targetPluginsDir, { recursive: true })

  for (const { bytes, spec } of staged) {
    const pluginDir = join(targetPluginsDir, spec.id)
    mkdirSync(pluginDir, { recursive: true })

    for (const entry of readdirSync(pluginDir)) {
      if (entry !== 'plugin.js') {
        rmSync(join(pluginDir, entry), { force: true, recursive: true })
      }
    }

    const targetFile = join(pluginDir, 'plugin.js')

    if (!existsSync(targetFile) || Buffer.compare(readFileSync(targetFile), bytes) !== 0) {
      writeFileSync(targetFile, bytes)
    }
  }

  const manifest = {
    schemaVersion: 1,
    plugins: staged.map(({ sha256, spec, syncedAtMs }) => ({
      id: spec.id,
      packageName: spec.packageName,
      sha256,
      syncedAtMs,
      sourceHint: spec.sourceHint
    }))
  }

  const serialized = JSON.stringify(manifest, null, 2) + '\n'

  if (!existsSync(manifestPath) || readFileSync(manifestPath, 'utf8') !== serialized) {
    writeFileSync(manifestPath, serialized, 'utf8')
  }

  return manifest
}

export function checkDesktopPlugins({ repoRoot = DEFAULT_REPO_ROOT } = {}) {
  const errors = []
  const targetPluginsDir = resolve(repoRoot, 'apps', 'desktop', 'src', 'plugins')
  const manifestPath = join(targetPluginsDir, 'org-plugins.manifest.json')

  if (!existsSync(manifestPath)) {
    return [`Missing manifest: ${manifestPath}`]
  }

  let manifest
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  } catch (error) {
    return [`Invalid JSON in ${manifestPath}: ${error instanceof Error ? error.message : String(error)}`]
  }

  if (!manifest || manifest.schemaVersion !== 1 || !Array.isArray(manifest.plugins)) {
    return [`Invalid manifest structure in ${manifestPath}: expected schemaVersion === 1 and plugins array`]
  }

  const expectedIds = ORG_PLUGINS.map(p => p.id)
  const actualIds = manifest.plugins.map(p => p?.id)

  if (JSON.stringify(actualIds) !== JSON.stringify(expectedIds)) {
    errors.push(`Manifest plugin ids mismatch: expected ${JSON.stringify(expectedIds)}, got ${JSON.stringify(actualIds)}`)
  }

  const crewAppJs = join(targetPluginsDir, 'crew', 'app.js')

  if (existsSync(crewAppJs)) {
    errors.push(`Prohibited standalone bundle present: ${crewAppJs}`)
  }

  for (const spec of ORG_PLUGINS) {
    const entry = manifest.plugins.find(p => p?.id === spec.id)
    const pluginDir = join(targetPluginsDir, spec.id)
    const pluginFile = join(pluginDir, 'plugin.js')

    if (!entry) {
      errors.push(`${spec.id}: missing entry in org-plugins.manifest.json`)
      continue
    }

    if (entry.packageName !== spec.packageName) {
      errors.push(`${spec.id}: expected packageName "${spec.packageName}", got "${String(entry.packageName)}"`)
    }

    if (typeof entry.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(entry.sha256)) {
      errors.push(`${spec.id}: invalid sha256 "${String(entry.sha256)}"`)
    }

    if (typeof entry.syncedAtMs !== 'number' || !Number.isFinite(entry.syncedAtMs) || entry.syncedAtMs <= 0) {
      errors.push(`${spec.id}: invalid syncedAtMs "${String(entry.syncedAtMs)}"`)
    }

    if (typeof entry.sourceHint !== 'string' || entry.sourceHint.trim().length === 0) {
      errors.push(`${spec.id}: invalid sourceHint`)
    }

    if (!existsSync(pluginDir)) {
      errors.push(`${spec.id}: missing bundled directory ${pluginDir}`)
      continue
    }

    for (const fileName of readdirSync(pluginDir)) {
      if (fileName !== 'plugin.js') {
        errors.push(`${spec.id}: prohibited extra file in bundled plugin folder: ${join(pluginDir, fileName)}`)
      }
    }

    if (!isRegularNonEmptyFile(pluginFile)) {
      errors.push(`${spec.id}: missing or empty bundled file ${pluginFile}`)
      continue
    }

    const bytes = readFileSync(pluginFile)
    const actualSha = sha256Buffer(bytes)

    if (actualSha !== entry.sha256) {
      errors.push(`${spec.id}: sha256 digest mismatch (manifest=${entry.sha256}, actual=${actualSha})`)
    }

    const unsupported = findUnsupportedImports(bytes.toString('utf8'))

    if (unsupported.length > 0) {
      errors.push(`${spec.id}: disallowed import specifier(s): ${unsupported.join(', ')}`)
    }
  }

  return errors
}

function main() {
  const { values } = parseArgs({
    options: {
      check: { default: false, type: 'boolean' },
      'plugins-root': { type: 'string' },
      'repo-root': { type: 'string' },
      'source-map': { type: 'string' },
      touch: { default: false, type: 'boolean' }
    },
    strict: true
  })

  const repoRoot = values['repo-root'] ? resolve(values['repo-root']) : DEFAULT_REPO_ROOT

  if (values.check) {
    const errors = checkDesktopPlugins({ repoRoot })

    if (errors.length > 0) {
      for (const err of errors) {
        console.error(`[sync-desktop-plugins --check] ${err}`)
      }

      process.exit(1)
    }

    console.log(`[sync-desktop-plugins --check] OK (${ORG_PLUGINS.map(p => p.id).join(', ')})`)
    return
  }

  const sourceMap = values['source-map'] ? JSON.parse(values['source-map']) : undefined
  const manifest = syncDesktopPlugins({
    pluginsRoot: values['plugins-root'],
    repoRoot,
    sourceMap,
    touch: values.touch
  })

  console.log(
    `[sync-desktop-plugins] Synced ${manifest.plugins.length} bundled org plugins (${manifest.plugins.map(p => `${p.id}:${p.sha256.slice(0, 8)}`).join(', ')})`
  )
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main()
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error))
    process.exit(1)
  }
}
