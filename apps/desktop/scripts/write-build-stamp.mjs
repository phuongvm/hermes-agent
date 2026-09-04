/**
 * Writes apps/desktop/build/install-stamp.json with the git ref and canonical
 * version the desktop .exe should pin to at first-launch bootstrap time and
 * runtime version reporting. This file ships inside the packaged app via
 * electron-builder's extraResources entry and is read by electron/main.ts.
 *
 * Schema (subject to bump via STAMP_SCHEMA_VERSION):
 *   {
 *     "schemaVersion": 1,
 *     "version":       "<canonical SemVer>",
 *     "commit":        "<40-char SHA>",
 *     "shortCommit":   "<8-char hex>",
 *     "buildNumber":   <non-negative integer>,
 *     "branch":        "<branch name>",
 *     "builtAt":       "<ISO 8601 UTC timestamp>",
 *     "dirty":         true|false,
 *     "source":        "ci" | "local" | "fallback"
 *   }
 *
 * Source preference order:
 *   1. Canonical version: root `pyproject.toml` (`[project] version = "..."`),
 *      falling back to `hermes_cli/__init__.py` (`__version__ = "..."`).
 *      Validates that Major, Minor, and Patch are unsigned 16-bit integers
 *      (0 <= x <= 65535).
 *   2. Git metadata:
 *      a. CI env vars ($GITHUB_SHA / $GITHUB_REF_NAME) -- avoid edge cases with
 *         shallow clones, detached HEADs, etc. in CI.
 *      b. Local `git rev-parse` against the repo root.
 *      c. Fallback stamp for local/personal builds from non-git source trees
 *         (ZIP extract, interrupted clone with no HEAD, etc.).
 *
 * Zero-touch invariant:
 *   Does NOT modify tracked workspace manifests (package.json, package-lock.json).
 *   Dirty status is determined before writing build/install-stamp.json.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs"
import { resolve, join, relative } from "path"
import { execSync } from "child_process"

import { isMain } from "./utils.mjs"

export const STAMP_SCHEMA_VERSION = 1

/** All-zero placeholder used when no real commit can be resolved. */
export const FALLBACK_COMMIT = "0000000000000000000000000000000000000000"
export const FALLBACK_SHORT_COMMIT = "00000000"
export const FALLBACK_BRANCH = "main"
export const FALLBACK_BUILD_NUMBER = 0

const DESKTOP_ROOT = resolve(import.meta.dirname, "..")
const REPO_ROOT = resolve(DESKTOP_ROOT, "..", "..")
const OUT_DIR = join(DESKTOP_ROOT, "build")
const OUT_FILE = join(OUT_DIR, "install-stamp.json")

function tryExec(cmd, opts) {
  try {
    return execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], ...opts }).trim()
  } catch {
    return null
  }
}

function tryReadFile(filePath) {
  try {
    if (!existsSync(filePath)) return null
    return readFileSync(filePath, "utf8")
  } catch {
    return null
  }
}

/**
 * Validate SemVer string and ensure major, minor, patch are within 0..65535.
 * Throws an error if malformed, negative, or exceeding 65535.
 */
export function validateSemVer(versionStr) {
  if (typeof versionStr !== "string" || !versionStr.trim()) {
    throw new Error(`Invalid version: expected non-empty string, got ${JSON.stringify(versionStr)}`)
  }
  const trimmed = versionStr.trim()
  // Match standard SemVer: major.minor.patch with optional prerelease and build metadata.
  // Note: negative components like -1 are rejected by regex.
  const semverRegex = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$/
  const match = trimmed.match(semverRegex)
  if (!match) {
    throw new Error(`Unsupported or malformed SemVer version: "${trimmed}"`)
  }

  const major = parseInt(match[1], 10)
  const minor = parseInt(match[2], 10)
  const patch = parseInt(match[3], 10)

  if (major < 0 || major > 65535 || minor < 0 || minor > 65535 || patch < 0 || patch > 65535) {
    throw new Error(
      `SemVer component out of 16-bit range [0..65535]: major=${major}, minor=${minor}, patch=${patch} in "${trimmed}"`
    )
  }

  return trimmed
}

/**
 * Extract canonical version from pyproject.toml (fallback to hermes_cli/__init__.py).
 */
export function resolveCanonicalVersion({ repoRoot = REPO_ROOT, readFileFn = tryReadFile } = {}) {
  // 1. Root pyproject.toml
  const pyprojectPath = join(repoRoot, "pyproject.toml")
  const pyprojectContent = readFileFn(pyprojectPath)
  if (pyprojectContent) {
    const match = pyprojectContent.match(/(?:^|\n)version\s*=\s*["']([^"']+)["']/)
    if (match && match[1]) {
      return validateSemVer(match[1])
    }
  }

  // 2. hermes_cli/__init__.py fallback
  const initPyPath = join(repoRoot, "hermes_cli", "__init__.py")
  const initPyContent = readFileFn(initPyPath)
  if (initPyContent) {
    const match = initPyContent.match(/(?:^|\n)__version__\s*=\s*["']([^"']+)["']/)
    if (match && match[1]) {
      return validateSemVer(match[1])
    }
  }

  throw new Error(
    `Cannot resolve canonical version: neither pyproject.toml nor hermes_cli/__init__.py provided a valid version at ${repoRoot}`
  )
}

export function fromCI(env = process.env) {
  const sha = env.GITHUB_SHA
  if (!sha) return null
  const branch = env.GITHUB_REF_NAME || env.GITHUB_HEAD_REF || null
  const shortCommit = sha.slice(0, 8)
  const rawBuildNum = env.HERMES_BUILD_NUMBER || env.GITHUB_RUN_NUMBER
  let buildNumber = 0
  if (rawBuildNum !== undefined && rawBuildNum !== null) {
    const parsed = parseInt(String(rawBuildNum), 10)
    if (!isNaN(parsed) && parsed >= 0) {
      buildNumber = parsed
    }
  }
  return {
    commit: sha,
    shortCommit,
    buildNumber,
    branch,
    dirty: false, // CI builds from a checkout-of-ref by definition
    source: "ci"
  }
}

export function fromLocalGit(repoRoot = REPO_ROOT, execFn = tryExec) {
  const sha = execFn("git rev-parse HEAD", { cwd: repoRoot })
  if (!sha) return null

  let shortCommit = execFn("git rev-parse --short=8 HEAD", { cwd: repoRoot })
  if (!shortCommit || !/^[0-9a-fA-F]{8}$/.test(shortCommit)) {
    shortCommit = sha.slice(0, 8)
  }

  const countStr = execFn("git rev-list --count HEAD", { cwd: repoRoot })
  let buildNumber = 0
  if (countStr) {
    const parsed = parseInt(countStr, 10)
    if (!isNaN(parsed) && parsed >= 0) {
      buildNumber = parsed
    }
  }

  const branch = execFn("git rev-parse --abbrev-ref HEAD", { cwd: repoRoot })
  // `git status --porcelain -uno` is empty iff tracked files match HEAD.
  // We exclude untracked files (-uno) intentionally: a developer who's
  // checked out an installer scratch dir alongside the repo shouldn't
  // poison every local build with a [DIRTY] stamp. We DO care about
  // tracked-but-modified files because those mean the .exe content
  // differs from the commit being pinned.
  const status = execFn("git status --porcelain -uno", { cwd: repoRoot })
  const dirty = status !== null && status.length > 0

  return {
    commit: sha,
    shortCommit,
    buildNumber,
    branch: branch === "HEAD" ? null : branch, // detached HEAD -> null
    dirty,
    source: "local"
  }
}

export function fromFallback(branch = FALLBACK_BRANCH) {
  // Non-git builds (ZIP download, bootstrap installer without a resolvable
  // HEAD) cannot determine a real commit. Use a placeholder so local /
  // personal builds can still complete.
  return {
    commit: FALLBACK_COMMIT,
    shortCommit: FALLBACK_SHORT_COMMIT,
    buildNumber: FALLBACK_BUILD_NUMBER,
    branch: branch || FALLBACK_BRANCH,
    dirty: false,
    source: "fallback"
  }
}

/**
 * Resolve the install stamp without writing it. Pure enough for unit tests:
 * inject env / execFn / repoRoot / readFileFn to simulate CI, local git, or no-git trees.
 */
export function resolveStamp({
  env = process.env,
  repoRoot = REPO_ROOT,
  execFn = tryExec,
  readFileFn = tryReadFile,
  fallbackBranch = FALLBACK_BRANCH
} = {}) {
  const gitMeta = fromCI(env) || fromLocalGit(repoRoot, execFn) || fromFallback(fallbackBranch)
  let version = null
  try {
    version = resolveCanonicalVersion({ repoRoot, readFileFn })
  } catch (err) {
    // If running in a test context where repoRoot may not have pyproject.toml,
    // allow caller to pass or let error bubble if required.
    throw err
  }

  return {
    version,
    ...gitMeta
  }
}

export function isFallbackCommit(commit) {
  return typeof commit === "string" && /^0{7,40}$/.test(commit)
}

function main() {
  const stamp = resolveStamp()
  if (!stamp || !stamp.commit) {
    console.error(
      "[write-build-stamp] ERROR: could not determine git commit.\n" +
        "  - $GITHUB_SHA not set\n" +
        "  - `git rev-parse HEAD` failed at " +
        REPO_ROOT +
        "\n" +
        "Packaged builds require a git ref to pin first-launch install.ps1\n" +
        "against. Run from a git checkout or set $GITHUB_SHA explicitly."
    )
    process.exit(1)
  }

  if (isFallbackCommit(stamp.commit)) {
    console.warn(
      "[write-build-stamp] WARNING: no git commit found (non-git checkout?).\n" +
        "  Using placeholder commit — the packaged app will fall back to the\n" +
        "  default branch for first-launch bootstrap. For production builds,\n" +
        "  run from a git checkout or set $GITHUB_SHA."
    )
  }

  if (stamp.dirty) {
    console.warn(
      "[write-build-stamp] WARNING: working tree is dirty.\n" +
        "  Pinning to " +
        stamp.commit.slice(0, 12) +
        " but the packaged code may differ from that commit.\n" +
        "  Commit your changes before publishing this build."
    )
  }

  const payload = {
    schemaVersion: STAMP_SCHEMA_VERSION,
    version: stamp.version,
    commit: stamp.commit,
    shortCommit: stamp.shortCommit,
    buildNumber: stamp.buildNumber,
    branch: stamp.branch,
    builtAt: new Date().toISOString(),
    dirty: stamp.dirty,
    source: stamp.source
  }

  mkdirSync(OUT_DIR, { recursive: true })
  writeFileSync(OUT_FILE, JSON.stringify(payload, null, 2) + "\n", "utf8")
  console.log(
    "[write-build-stamp] wrote " +
      relative(REPO_ROOT, OUT_FILE) +
      " -> " +
      stamp.version +
      " (" +
      stamp.shortCommit +
      ")" +
      " #" +
      stamp.buildNumber +
      (stamp.branch ? " (" + stamp.branch + ")" : "") +
      (stamp.dirty ? " [DIRTY]" : "") +
      (stamp.source === "fallback" ? " [FALLBACK]" : "")
  )
}

if (isMain(import.meta.url)) {
  main()
}
