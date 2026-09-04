import assert from 'node:assert/strict'
import { test } from 'vitest'

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

import { buildElectronBuilderArgs } from './run-electron-builder.mjs'
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
    buildNumber: 27745
  }
  const args = buildElectronBuilderArgs({ stamp, extraArgs: ['--win', 'nsis'] })
  assert.ok(args.includes('-c.extraMetadata.version=0.21.0'))
  assert.ok(args.includes('-c.buildVersion=0.21.0.27745'))
  assert.ok(args.includes('-c.buildNumber=27745'))
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
