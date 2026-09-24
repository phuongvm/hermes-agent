import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, vi } from 'vitest'

import {
  FALLBACK_BRANCH,
  FALLBACK_COMMIT,
  FALLBACK_SHORT_COMMIT,
  FALLBACK_BUILD_NUMBER,
  fromCI,
  fromFallback,
  fromLocalGit,
  isFallbackCommit,
  resolveStamp,
  resolveCanonicalVersion,
  validateSemVer
} from './write-build-stamp.mjs'

import { assertVersionAlignment, buildElectronBuilderArgs, prepareBuilderArgs } from './run-electron-builder.mjs'
import { buildRceditOptions } from './set-exe-identity.mjs'

test('fromCI reads GITHUB_SHA / GITHUB_REF_NAME / GITHUB_RUN_NUMBER', () => {
  assert.deepEqual(
    fromCI({
      GITHUB_SHA: 'a'.repeat(40),
      GITHUB_REF_NAME: 'release',
      GITHUB_RUN_NUMBER: '123'
    }),
    {
      commit: 'a'.repeat(40),
      shortCommit: 'aaaaaaaa',
      buildNumber: 123,
      branch: 'release',
      dirty: false,
      source: 'ci'
    }
  )
  assert.equal(fromCI({}), null)
})

test('fromLocalGit returns null when git rev-parse fails', () => {
  const stamp = fromLocalGit('/tmp/not-a-repo', () => null)
  assert.equal(stamp, null)
})

test('fromLocalGit reads HEAD + shortCommit + commit count + branch + dirty status', () => {
  const calls = []
  const execFn = (cmd) => {
    calls.push(cmd)
    if (cmd === 'git rev-parse HEAD') return 'b'.repeat(40)
    if (cmd === 'git rev-parse --short=8 HEAD') return 'bbbbbbbb'
    if (cmd === 'git rev-list --count HEAD') return '27745'
    if (cmd === 'git rev-parse --abbrev-ref HEAD') return 'main'
    if (cmd === 'git status --porcelain -uno') return ' M apps/desktop/package.json'
    return null
  }
  assert.deepEqual(fromLocalGit('/repo', execFn), {
    commit: 'b'.repeat(40),
    shortCommit: 'bbbbbbbb',
    buildNumber: 27745,
    branch: 'main',
    dirty: true,
    source: 'local'
  })
  assert.ok(calls.includes('git rev-parse HEAD'))
  assert.ok(calls.includes('git rev-parse --short=8 HEAD'))
  assert.ok(calls.includes('git rev-list --count HEAD'))
})

test('fromFallback uses the all-zero placeholder commit and shortCommit', () => {
  assert.deepEqual(fromFallback(), {
    commit: FALLBACK_COMMIT,
    shortCommit: FALLBACK_SHORT_COMMIT,
    buildNumber: FALLBACK_BUILD_NUMBER,
    branch: FALLBACK_BRANCH,
    dirty: false,
    source: 'fallback'
  })
  assert.equal(isFallbackCommit(FALLBACK_COMMIT), true)
  assert.equal(isFallbackCommit('a'.repeat(40)), false)
})

test('resolveStamp prefers CI over local git over fallback', () => {
  const mockReadFile = (p) => {
    if (p.endsWith('pyproject.toml')) {
      return '[project]\nname = "hermes-agent"\nversion = "0.21.0"\n'
    }
    return null
  }

  const ci = resolveStamp({
    env: { GITHUB_SHA: 'c'.repeat(40), GITHUB_REF_NAME: 'main', GITHUB_RUN_NUMBER: '42' },
    readFileFn: mockReadFile,
    execFn: () => 'should-not-run'
  })
  assert.equal(ci.source, 'ci')
  assert.equal(ci.commit, 'c'.repeat(40))
  assert.equal(ci.shortCommit, 'cccccccc')
  assert.equal(ci.buildNumber, 42)
  assert.equal(ci.version, '0.21.0')

  const local = resolveStamp({
    env: {},
    readFileFn: mockReadFile,
    execFn: (cmd) => {
      if (cmd === 'git rev-parse HEAD') return 'd'.repeat(40)
      if (cmd === 'git rev-parse --short=8 HEAD') return 'dddddddd'
      if (cmd === 'git rev-list --count HEAD') return '100'
      if (cmd === 'git rev-parse --abbrev-ref HEAD') return 'main'
      if (cmd === 'git status --porcelain -uno') return ''
      return null
    }
  })
  assert.equal(local.source, 'local')
  assert.equal(local.commit, 'd'.repeat(40))
  assert.equal(local.shortCommit, 'dddddddd')
  assert.equal(local.buildNumber, 100)
  assert.equal(local.dirty, false)
  assert.equal(local.version, '0.21.0')
})

test('resolveStamp falls back when neither CI nor git is available', () => {
  const mockReadFile = (p) => {
    if (p.endsWith('pyproject.toml')) {
      return '[project]\nname = "hermes-agent"\nversion = "0.21.0"\n'
    }
    return null
  }

  const stamp = resolveStamp({ env: {}, readFileFn: mockReadFile, execFn: () => null })
  assert.deepEqual(stamp, {
    version: '0.21.0',
    commit: FALLBACK_COMMIT,
    shortCommit: FALLBACK_SHORT_COMMIT,
    buildNumber: 0,
    branch: FALLBACK_BRANCH,
    dirty: false,
    source: 'fallback'
  })
})

// --- Phase 4.1: Explicit Unit Tests for Versioning & Boundaries ---

test('(a) Canonical version parsing from pyproject.toml: valid SemVer vs malformed rejection', () => {
  const validPyproject = `
[project]
name = "hermes-agent"
version = "0.21.0"
`
  const v = resolveCanonicalVersion({
    repoRoot: '/fake/root',
    readFileFn: (p) => (p.endsWith('pyproject.toml') ? validPyproject : null)
  })
  assert.equal(v, '0.21.0')

  // Malformed SemVer
  const malformedPyproject = `
[project]
name = "hermes-agent"
version = "not-a-semver"
`
  assert.throws(() => {
    resolveCanonicalVersion({
      repoRoot: '/fake/root',
      readFileFn: (p) => (p.endsWith('pyproject.toml') ? malformedPyproject : null)
    })
  }, /Unsupported or malformed SemVer version/)
})

test('(b) SemVer components exceeding 65535 or negative values fail fast', () => {
  // Exceeds 65535
  assert.throws(() => {
    validateSemVer('70000.0.1')
  }, /SemVer component out of 16-bit range/)

  assert.throws(() => {
    validateSemVer('1.65536.0')
  }, /SemVer component out of 16-bit range/)

  assert.throws(() => {
    validateSemVer('0.0.65536')
  }, /SemVer component out of 16-bit range/)

  // Negative values
  assert.throws(() => {
    validateSemVer('-1.0.0')
  }, /Unsupported or malformed SemVer version/)

  assert.throws(() => {
    validateSemVer('1.-2.3')
  }, /Unsupported or malformed SemVer version/)

  // Valid boundary values
  assert.equal(validateSemVer('0.0.0'), '0.0.0')
  assert.equal(validateSemVer('65535.65535.65535'), '65535.65535.65535')
})

test('(c) Git short SHA (8-char hex) and commit count extraction', () => {
  const execFn = (cmd) => {
    if (cmd === 'git rev-parse HEAD') return '1234567890abcdef1234567890abcdef12345678'
    if (cmd === 'git rev-parse --short=8 HEAD') return '12345678'
    if (cmd === 'git rev-list --count HEAD') return '4567'
    if (cmd === 'git rev-parse --abbrev-ref HEAD') return 'feat/desktop'
    if (cmd === 'git status --porcelain -uno') return ''
    return null
  }

  const meta = fromLocalGit('/fake/repo', execFn)
  assert.equal(meta.shortCommit, '12345678')
  assert.equal(meta.buildNumber, 4567)
  assert.equal(meta.branch, 'feat/desktop')
})

test('(d) Non-Git environment fallback (00000000 short SHA, 0 build number)', () => {
  const fallback = fromFallback('custom-branch')
  assert.equal(fallback.commit, '0000000000000000000000000000000000000000')
  assert.equal(fallback.shortCommit, '00000000')
  assert.equal(fallback.buildNumber, 0)
  assert.equal(fallback.branch, 'custom-branch')
  assert.equal(fallback.source, 'fallback')
})

test('(e) Precedence: pyproject.toml over hermes_cli/__init__.py', () => {
  const pyproject = `[project]\nversion = "0.21.0"\n`
  const initPy = `__version__ = "0.19.5"\n`

  const v = resolveCanonicalVersion({
    repoRoot: '/fake/root',
    readFileFn: (p) => {
      if (p.endsWith('pyproject.toml')) return pyproject
      if (p.endsWith('__init__.py')) return initPy
      return null
    }
  })
  assert.equal(v, '0.21.0')

  // When pyproject.toml is missing, falls back to __init__.py
  const fallbackV = resolveCanonicalVersion({
    repoRoot: '/fake/root',
    readFileFn: (p) => {
      if (p.endsWith('__init__.py')) return initPy
      return null
    }
  })
  assert.equal(fallbackV, '0.19.5')
})

// --- Phase 4.4: Builder Configuration & PE Stamping Unit Tests ---

test('buildElectronBuilderArgs generates dynamic arguments with modulo 65536 PE buildNumber', () => {
  // Normal build number under 65536
  const stamp = {
    version: '0.21.0',
    buildNumber: 27745,
    builtAt: '2026-09-10T08:00:00.000Z'
  }
  const args = buildElectronBuilderArgs({ stamp, extraArgs: ['--win', 'nsis'] })
  assert.ok(args.includes('-c.extraMetadata.version=0.21.0'))
  assert.ok(args.includes('-c.buildVersion=0.21.0.27745'))
  assert.ok(args.includes('-c.buildNumber=27745'))
  assert.ok(args.includes('-c.artifactName=Hermes-${version}-${os}-${arch}-2026-09-10.${ext}'))
  assert.ok(args.includes('--win'))
  assert.ok(args.includes('nsis'))

  // Overflow build number > 65535: 70000 % 65536 = 4464
  const stampOverflow = {
    version: '0.21.0',
    buildNumber: 70000
  }
  const argsOverflow = buildElectronBuilderArgs({ stamp: stampOverflow })
  assert.ok(argsOverflow.includes('-c.extraMetadata.version=0.21.0'))
  assert.ok(argsOverflow.includes('-c.buildVersion=0.21.0.4464'))
  assert.ok(argsOverflow.includes('-c.buildNumber=4464'))
})

test('buildRceditOptions constructs correct 4-tuple and string ProductVersion with modulo 65536', () => {
  const stamp = {
    version: '0.21.0',
    shortCommit: '28e38b39',
    buildNumber: 70000
  }
  const opts = buildRceditOptions(stamp, '/path/to/icon.ico')
  assert.equal(opts.icon, '/path/to/icon.ico')
  assert.equal(opts['file-version'], '0.21.0.4464')
  assert.equal(opts['product-version'], '0.21.0.4464')
  assert.equal(opts['version-string'].ProductVersion, '0.21.0 (28e38b39)')
  assert.equal(opts['version-string'].FileVersion, '0.21.0')
  assert.equal(opts['version-string'].ProductName, 'Hermes')
})

// --- Group 3: Pre-Build Drift Guard Tests (assertVersionAlignment) ---

test('3.1 assertVersionAlignment: all versions equal -> { ok: true, version }', () => {
  const res = assertVersionAlignment({
    packageJsonVersion: '0.21.3',
    pyprojectVersion: '0.21.3',
    stampVersion: '0.21.3'
  })
  assert.deepEqual(res, { ok: true, version: '0.21.3' })
})

test('3.2 assertVersionAlignment: package.json=0.17.6 vs 0.21.3/0.21.3 throws with all three pairs and remediation', () => {
  assert.throws(
    () => {
      assertVersionAlignment({
        packageJsonVersion: '0.17.6',
        pyprojectVersion: '0.21.3',
        stampVersion: '0.21.3'
      })
    },
    (err) => {
      assert.ok(err instanceof Error)
      assert.ok(err.message.includes('package.json=0.17.6'))
      assert.ok(err.message.includes('pyproject.toml=0.21.3'))
      assert.ok(err.message.includes('install-stamp.json=0.21.3'))
      assert.ok(
        err.message.includes('align apps/desktop/package.json "version" with pyproject.toml')
      )
      return true
    }
  )
})

test('3.3 assertVersionAlignment: stampVersion null/undefined throws with run `npm run build` guidance', () => {
  assert.throws(
    () => {
      assertVersionAlignment({
        packageJsonVersion: '0.21.3',
        pyprojectVersion: '0.21.3',
        stampVersion: null
      })
    },
    (err) => {
      assert.ok(err instanceof Error)
      assert.ok(err.message.includes('run `npm run build` first') || err.message.includes('npm run build'))
      return true
    }
  )

  assert.throws(
    () => {
      assertVersionAlignment({
        packageJsonVersion: '0.21.3',
        pyprojectVersion: '0.21.3',
        stampVersion: undefined
      })
    },
    (err) => {
      assert.ok(err instanceof Error)
      assert.ok(err.message.includes('run `npm run build` first') || err.message.includes('npm run build'))
      return true
    }
  )
})

test('3.4 assertVersionAlignment: stampVersion invalid SemVer throws naming the value', () => {
  assert.throws(
    () => {
      assertVersionAlignment({
        packageJsonVersion: '0.21.3',
        pyprojectVersion: '0.21.3',
        stampVersion: 'abc'
      })
    },
    (err) => {
      assert.ok(err instanceof Error)
      assert.ok(err.message.includes('abc'))
      return true
    }
  )
})

test('3.5 assertVersionAlignment: drift + allowDrift: true does not throw, returns { ok: false, version: <pyproject> } and warns once', () => {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    const res = assertVersionAlignment({
      packageJsonVersion: '0.17.6',
      pyprojectVersion: '0.21.3',
      stampVersion: '0.21.3',
      allowDrift: true
    })
    assert.deepEqual(res, { ok: false, version: '0.21.3' })
    assert.equal(warnSpy.mock.calls.length, 1)
    const warned = warnSpy.mock.calls[0][0]
    assert.ok(warned.includes('package.json=0.17.6'))
    assert.ok(warned.includes('pyproject.toml=0.21.3'))
    assert.ok(warned.includes('install-stamp.json=0.21.3'))
  } finally {
    warnSpy.mockRestore()
  }
})

test('3.6 assertVersionAlignment: guard does not touch fs or process.env', () => {
  const readSpy = vi.spyOn(fs, 'readFileSync')
  const existsSpy = vi.spyOn(fs, 'existsSync')
  const originalEnv = { ...process.env }

  try {
    const res = assertVersionAlignment({
      packageJsonVersion: '0.21.3',
      pyprojectVersion: '0.21.3',
      stampVersion: '0.21.3',
      allowDrift: false
    })
    assert.deepEqual(res, { ok: true, version: '0.21.3' })

    assert.equal(readSpy.mock.calls.length, 0)
    assert.equal(existsSpy.mock.calls.length, 0)
  } finally {
    readSpy.mockRestore()
    existsSpy.mockRestore()
    process.env = originalEnv
  }
})

test('3.8 assertVersionAlignment: stampVersion null + allowDrift: true returns canonical and warns', () => {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    const res = assertVersionAlignment({
      packageJsonVersion: '0.21.3',
      pyprojectVersion: '0.21.3',
      stampVersion: null,
      allowDrift: true
    })
    assert.deepEqual(res, { ok: false, version: '0.21.3' })
    assert.equal(warnSpy.mock.calls.length, 1)
    const warned = warnSpy.mock.calls[0][0]
    assert.ok(warned.includes('install-stamp.json=<missing>'))
    assert.ok(warned.includes('run `npm run build` first'))
  } finally {
    warnSpy.mockRestore()
  }
})

test('3.9 assertVersionAlignment: stampVersion invalid + allowDrift: true returns canonical and warns', () => {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    const res = assertVersionAlignment({
      packageJsonVersion: '0.21.3',
      pyprojectVersion: '0.21.3',
      stampVersion: 'abc',
      allowDrift: true
    })
    assert.deepEqual(res, { ok: false, version: '0.21.3' })
    assert.equal(warnSpy.mock.calls.length, 1)
    const warned = warnSpy.mock.calls[0][0]
    assert.ok(warned.includes('abc'))
  } finally {
    warnSpy.mockRestore()
  }
})

test('3.10 assertVersionAlignment: packageJsonVersion invalid/missing + allowDrift: true returns canonical and warns', () => {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    const resMissing = assertVersionAlignment({
      packageJsonVersion: null,
      pyprojectVersion: '0.21.3',
      stampVersion: '0.21.3',
      allowDrift: true
    })
    assert.deepEqual(resMissing, { ok: false, version: '0.21.3' })

    const resInvalid = assertVersionAlignment({
      packageJsonVersion: 'xyz',
      pyprojectVersion: '0.21.3',
      stampVersion: '0.21.3',
      allowDrift: true
    })
    assert.deepEqual(resInvalid, { ok: false, version: '0.21.3' })
    assert.equal(warnSpy.mock.calls.length, 2)
  } finally {
    warnSpy.mockRestore()
  }
})

test('3.11 assertVersionAlignment: missing or invalid pyprojectVersion throws even if allowDrift: true', () => {
  assert.throws(
    () => {
      assertVersionAlignment({
        packageJsonVersion: '0.21.3',
        pyprojectVersion: null,
        stampVersion: '0.21.3',
        allowDrift: true
      })
    },
    /cannot resolve valid canonical version/i
  )

  assert.throws(
    () => {
      assertVersionAlignment({
        packageJsonVersion: '0.21.3',
        pyprojectVersion: 'bad',
        stampVersion: '0.21.3',
        allowDrift: true
      })
    },
    /cannot resolve valid canonical version/i
  )
})

test('3.12 prepareBuilderArgs: stale stamp version with allowDrift packages canonical version across builder args', () => {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    const prepared = prepareBuilderArgs({
      packageJsonVersion: '0.21.3',
      pyprojectVersion: '0.21.3',
      stamp: {
        version: '0.20.0',
        buildNumber: 1234,
        builtAt: '2026-09-24T12:00:00.000Z'
      },
      allowDrift: true
    })

    assert.equal(prepared.alignment.ok, false)
    assert.equal(prepared.alignment.version, '0.21.3')
    assert.ok(prepared.args.includes('-c.extraMetadata.version=0.21.3'))
    assert.ok(prepared.args.includes('-c.buildVersion=0.21.3.1234'))
    assert.ok(prepared.args.includes('-c.artifactName=Hermes-${version}-${os}-${arch}-2026-09-24.${ext}'))
    // Must NOT contain stale stamp version
    assert.ok(!prepared.args.includes('-c.extraMetadata.version=0.20.0'))
    assert.ok(!prepared.args.includes('-c.buildVersion=0.20.0.1234'))
  } finally {
    warnSpy.mockRestore()
  }
})

test('3.13 prepareBuilderArgs: null stamp with allowDrift packages canonical version', () => {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    const prepared = prepareBuilderArgs({
      packageJsonVersion: '0.21.3',
      pyprojectVersion: '0.21.3',
      stamp: null,
      allowDrift: true
    })

    assert.equal(prepared.alignment.ok, false)
    assert.equal(prepared.alignment.version, '0.21.3')
    assert.ok(prepared.args.includes('-c.extraMetadata.version=0.21.3'))
    assert.ok(prepared.args.includes('-c.buildVersion=0.21.3.0'))
  } finally {
    warnSpy.mockRestore()
  }
})

// --- Child Process Regression Tests ---

const SCRIPT_PATH = path.resolve(import.meta.dirname, 'run-electron-builder.mjs')

function makeTempFixture({ packageVersion = '0.21.3', pyprojectVersion = '0.21.3', stampVersion = '0.21.3' } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'drift-test-'))
  const desktopDir = path.join(dir, 'apps', 'desktop')
  fs.mkdirSync(desktopDir, { recursive: true })

  if (packageVersion !== undefined) {
    if (packageVersion === 'MALFORMED') {
      fs.writeFileSync(path.join(desktopDir, 'package.json'), '{ "version": invalid json')
    } else {
      fs.writeFileSync(path.join(desktopDir, 'package.json'), JSON.stringify({ name: 'hermes', version: packageVersion }))
    }
  }

  if (pyprojectVersion !== undefined) {
    if (pyprojectVersion === 'MALFORMED') {
      fs.writeFileSync(path.join(dir, 'pyproject.toml'), '[project]\nversion = "not-a-semver"')
    } else if (pyprojectVersion !== null) {
      fs.writeFileSync(path.join(dir, 'pyproject.toml'), `[project]\nversion = "${pyprojectVersion}"\n`)
    }
  }

  if (stampVersion !== undefined && stampVersion !== null) {
    const buildDir = path.join(desktopDir, 'build')
    fs.mkdirSync(buildDir, { recursive: true })
    fs.writeFileSync(path.join(buildDir, 'install-stamp.json'), JSON.stringify({ version: stampVersion, buildNumber: 10 }))
  }

  return { rootDir: dir, desktopDir }
}

test('3.14 child process: version drift without bypass exits 1 with single actionable line, no spawn', () => {
  const fixture = makeTempFixture({ packageVersion: '0.17.6', pyprojectVersion: '0.21.3', stampVersion: '0.21.3' })
  try {
    const res = spawnSync(process.execPath, [SCRIPT_PATH], {
      env: {
        ...process.env,
        HERMES_DESKTOP_ROOT: fixture.desktopDir,
        HERMES_REPO_ROOT: fixture.rootDir,
        HERMES_DESKTOP_ALLOW_VERSION_DRIFT: '0'
      },
      encoding: 'utf8'
    })

    assert.equal(res.status, 1)
    const stderr = res.stderr.trim()
    assert.ok(stderr.includes('[run-electron-builder] version drift: package.json=0.17.6 pyproject.toml=0.21.3 install-stamp.json=0.21.3'))
    assert.ok(stderr.includes('align apps/desktop/package.json "version" with pyproject.toml'))
    // Must be a single actionable line without stack trace
    assert.equal(stderr.split('\n').filter(Boolean).length, 1)
    assert.ok(!stderr.includes('at main ('))
    assert.ok(!res.stdout.includes('running electron-builder with args:'))
  } finally {
    fs.rmSync(fixture.rootDir, { recursive: true, force: true })
  }
})

test('3.15 child process: malformed package.json exits 1 with single actionable line, no stack trace', () => {
  const fixture = makeTempFixture({ packageVersion: 'MALFORMED', pyprojectVersion: '0.21.3', stampVersion: '0.21.3' })
  try {
    const res = spawnSync(process.execPath, [SCRIPT_PATH], {
      env: {
        ...process.env,
        HERMES_DESKTOP_ROOT: fixture.desktopDir,
        HERMES_REPO_ROOT: fixture.rootDir,
        HERMES_DESKTOP_ALLOW_VERSION_DRIFT: '0'
      },
      encoding: 'utf8'
    })

    assert.equal(res.status, 1)
    const stderr = res.stderr.trim()
    assert.ok(stderr.includes('[run-electron-builder] failed to read'))
    assert.equal(stderr.split('\n').filter(Boolean).length, 1)
    assert.ok(!stderr.includes('at main ('))
    assert.ok(!res.stdout.includes('running electron-builder with args:'))
  } finally {
    fs.rmSync(fixture.rootDir, { recursive: true, force: true })
  }
})

test('3.16 child process: unresolvable canonical version exits 1 with single actionable line, no stack trace', () => {
  const fixture = makeTempFixture({ packageVersion: '0.21.3', pyprojectVersion: null, stampVersion: '0.21.3' })
  try {
    const res = spawnSync(process.execPath, [SCRIPT_PATH], {
      env: {
        ...process.env,
        HERMES_DESKTOP_ROOT: fixture.desktopDir,
        HERMES_REPO_ROOT: fixture.rootDir,
        HERMES_DESKTOP_ALLOW_VERSION_DRIFT: '0'
      },
      encoding: 'utf8'
    })

    assert.equal(res.status, 1)
    const stderr = res.stderr.trim()
    assert.ok(stderr.includes('[run-electron-builder] Cannot resolve canonical version'))
    assert.equal(stderr.split('\n').filter(Boolean).length, 1)
    assert.ok(!stderr.includes('at main ('))
    assert.ok(!res.stdout.includes('running electron-builder with args:'))
  } finally {
    fs.rmSync(fixture.rootDir, { recursive: true, force: true })
  }
})

test('3.17 child process: missing stamp without bypass exits 1 with single actionable line', () => {
  const fixture = makeTempFixture({ packageVersion: '0.21.3', pyprojectVersion: '0.21.3', stampVersion: null })
  try {
    const res = spawnSync(process.execPath, [SCRIPT_PATH], {
      env: {
        ...process.env,
        HERMES_DESKTOP_ROOT: fixture.desktopDir,
        HERMES_REPO_ROOT: fixture.rootDir,
        HERMES_DESKTOP_ALLOW_VERSION_DRIFT: '0'
      },
      encoding: 'utf8'
    })

    assert.equal(res.status, 1)
    const stderr = res.stderr.trim()
    assert.ok(stderr.includes('install-stamp.json=<missing>'))
    assert.ok(stderr.includes('run `npm run build` first'))
    assert.equal(stderr.split('\n').filter(Boolean).length, 1)
    assert.ok(!stderr.includes('at main ('))
    assert.ok(!res.stdout.includes('running electron-builder with args:'))
  } finally {
    fs.rmSync(fixture.rootDir, { recursive: true, force: true })
  }
})

test('3.18 child process: version drift with allowDrift spawns builder with canonical args', () => {
  const fixture = makeTempFixture({ packageVersion: '0.17.6', pyprojectVersion: '0.21.3', stampVersion: '0.20.0' })
  const mockBinPath = path.join(fixture.rootDir, 'mock-builder.mjs')
  const recordPath = path.join(fixture.rootDir, 'builder-args.json')
  fs.writeFileSync(
    mockBinPath,
    `import fs from "node:fs"\nfs.writeFileSync(${JSON.stringify(recordPath)}, JSON.stringify(process.argv.slice(2)))\nprocess.exit(0)\n`
  )

  try {
    const res = spawnSync(process.execPath, [SCRIPT_PATH, '--publish', 'never'], {
      env: {
        ...process.env,
        HERMES_DESKTOP_ROOT: fixture.desktopDir,
        HERMES_REPO_ROOT: fixture.rootDir,
        HERMES_DESKTOP_ALLOW_VERSION_DRIFT: '1',
        HERMES_ELECTRON_BUILDER_BIN: mockBinPath
      },
      encoding: 'utf8'
    })

    assert.equal(res.status, 0)
    assert.ok(fs.existsSync(recordPath))
    const capturedArgs = JSON.parse(fs.readFileSync(recordPath, 'utf8'))

    // Proves canonical version was wired into builder args
    assert.ok(capturedArgs.includes('-c.extraMetadata.version=0.21.3'))
    assert.ok(capturedArgs.includes('-c.buildVersion=0.21.3.10'))
    assert.ok(!capturedArgs.includes('-c.extraMetadata.version=0.20.0'))
    assert.ok(!capturedArgs.includes('-c.extraMetadata.version=0.17.6'))

    // Proves warning was output
    assert.ok(res.stderr.includes('[run-electron-builder] version drift: package.json=0.17.6 pyproject.toml=0.21.3 install-stamp.json=0.20.0'))
  } finally {
    fs.rmSync(fixture.rootDir, { recursive: true, force: true })
  }
})
