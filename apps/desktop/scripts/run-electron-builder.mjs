// Resolve electronDist at runtime (#38673, #47917): electron-builder 26.8.x can
// re-unpack a broken Electron.app; reusing the installed dist dodges that.
// npm workspace hoisting is non-deterministic — require.resolve finds electron
// wherever it landed. Dist present → -c.electronDist=<abs>/dist; absent → let
// electron-builder fetch via @electron/get (electronVersion + ELECTRON_MIRROR).

import fs from "node:fs"
import path from "node:path"
import { spawnSync } from "node:child_process"
import { createRequire } from "node:module"
import { isMain } from "./utils.mjs"
import { validateSemVer, resolveCanonicalVersion } from "./write-build-stamp.mjs"

const require = createRequire(import.meta.url)

export function assertVersionAlignment({
  packageJsonVersion,
  pyprojectVersion,
  stampVersion,
  allowDrift = false
} = {}) {
  let validPy
  try {
    validPy = validateSemVer(pyprojectVersion)
  } catch (err) {
    throw new Error(
      `[run-electron-builder] cannot resolve valid canonical version: ${err.message}`
    )
  }

  let validPkg = null
  let pkgErr = null
  try {
    validPkg = validateSemVer(packageJsonVersion)
  } catch (err) {
    pkgErr = err
  }

  let validStamp = null
  let stampErr = null
  if (stampVersion == null) {
    stampErr = new Error("missing install-stamp.json version")
  } else {
    try {
      validStamp = validateSemVer(stampVersion)
    } catch (err) {
      stampErr = err
    }
  }

  if (pkgErr || stampErr || validPkg !== validPy || validStamp !== validPy) {
    const pkgDisplay =
      validPkg !== null
        ? validPkg
        : packageJsonVersion == null
          ? "<missing>"
          : String(packageJsonVersion)
    const stampDisplay =
      validStamp !== null
        ? validStamp
        : stampVersion == null
          ? "<missing>"
          : String(stampVersion)

    let remediation = 'align apps/desktop/package.json "version" with pyproject.toml'
    if (stampVersion == null) {
      remediation = "run `npm run build` first"
    } else if (stampErr) {
      remediation = "run `npm run build` to regenerate install-stamp.json"
    } else if (pkgErr) {
      remediation = 'specify a valid SemVer "version" in apps/desktop/package.json'
    }

    const message = `[run-electron-builder] version drift: package.json=${pkgDisplay} pyproject.toml=${validPy} install-stamp.json=${stampDisplay} — ${remediation}`
    if (allowDrift) {
      console.warn(message)
      return { ok: false, version: validPy }
    }
    throw new Error(message)
  }

  return { ok: true, version: validPy }
}

export function loadCanonicalVersionForBuilder(repoRoot = process.env.HERMES_REPO_ROOT) {
  try {
    return resolveCanonicalVersion(repoRoot ? { repoRoot } : undefined)
  } catch (err) {
    throw new Error(`[run-electron-builder] ${err.message}`)
  }
}

export function loadDesktopManifestVersion(
  desktopRoot = process.env.HERMES_DESKTOP_ROOT || path.resolve(import.meta.dirname, "..")
) {
  const pkgPath = path.join(desktopRoot, "package.json")
  try {
    const raw = fs.readFileSync(pkgPath, "utf8")
    const parsed = JSON.parse(raw)
    return parsed.version
  } catch (err) {
    throw new Error(`[run-electron-builder] failed to read ${pkgPath}: ${err.message}`)
  }
}

export function electronDistDir() {
  try {
    return path.join(path.dirname(require.resolve("electron/package.json")), "dist")
  } catch {
    return null
  }
}

export function distBinary(dist) {
  if (process.platform === "darwin") {
    return path.join(dist, "Electron.app", "Contents", "MacOS", "Electron")
  }
  if (process.platform === "win32") {
    return path.join(dist, "electron.exe")
  }
  return path.join(dist, "electron")
}

export function electronBuilderCli() {
  const pkgJson = require.resolve("electron-builder/package.json")
  const bin = require(pkgJson).bin
  const rel = typeof bin === "string" ? bin : bin["electron-builder"]
  return path.join(path.dirname(pkgJson), rel)
}

/**
 * Pure helper to compute electron-builder CLI arguments including dynamic
 * version and build metadata from install-stamp.json without mutating package.json.
 */
export function buildElectronBuilderArgs({
  stamp = null,
  dist = null,
  extraArgs = []
} = {}) {
  const args = ["--publish", "never"]

  if (dist && fs.existsSync(distBinary(dist))) {
    args.push(`-c.electronDist=${dist}`)
  }

  if (stamp && stamp.version) {
    const canonicalVersion = stamp.version
    const rawBuildNum = typeof stamp.buildNumber === "number" && !isNaN(stamp.buildNumber) && stamp.buildNumber >= 0
      ? stamp.buildNumber
      : 0
    // 16-bit integer boundary protection for numeric PE tuple component
    const peBuildNumber = rawBuildNum % 65536
    const buildDate = stamp.builtAt ? stamp.builtAt.slice(0, 10) : new Date().toISOString().slice(0, 10)

    args.push(`-c.extraMetadata.version=${canonicalVersion}`)
    args.push(`-c.buildVersion=${canonicalVersion}.${peBuildNumber}`)
    args.push(`-c.buildNumber=${peBuildNumber}`)
    args.push(`-c.artifactName=Hermes-\${version}-\${os}-\${arch}-${buildDate}.\${ext}`)
  }

  args.push(...extraArgs)
  return args
}

export function loadStampForBuilder(
  desktopRoot = process.env.HERMES_DESKTOP_ROOT || path.resolve(import.meta.dirname, "..")
) {
  const stampPath = path.join(desktopRoot, "build", "install-stamp.json")
  try {
    if (fs.existsSync(stampPath)) {
      const raw = fs.readFileSync(stampPath, "utf8")
      return JSON.parse(raw)
    }
  } catch (err) {
    console.warn(`[run-electron-builder] failed to read ${stampPath}: ${err.message}`)
  }
  return null
}

export function prepareBuilderArgs({
  desktopRoot = process.env.HERMES_DESKTOP_ROOT || path.resolve(import.meta.dirname, ".."),
  repoRoot = process.env.HERMES_REPO_ROOT ||
    (process.env.HERMES_DESKTOP_ROOT ? path.resolve(process.env.HERMES_DESKTOP_ROOT, "..", "..") : undefined),
  packageJsonVersion,
  pyprojectVersion,
  stamp,
  allowDrift = process.env.HERMES_DESKTOP_ALLOW_VERSION_DRIFT === "1",
  extraArgs = [],
  dist = null
} = {}) {
  const resolvedStamp = stamp !== undefined ? stamp : loadStampForBuilder(desktopRoot)
  const resolvedPkgVersion =
    packageJsonVersion !== undefined ? packageJsonVersion : loadDesktopManifestVersion(desktopRoot)
  const resolvedPyVersion =
    pyprojectVersion !== undefined ? pyprojectVersion : loadCanonicalVersionForBuilder(repoRoot)
  const stampVersion = resolvedStamp ? resolvedStamp.version : null

  const alignment = assertVersionAlignment({
    packageJsonVersion: resolvedPkgVersion,
    pyprojectVersion: resolvedPyVersion,
    stampVersion,
    allowDrift
  })

  // Wire canonical version into effective stamp so extraMetadata.version, buildVersion, and artifactName package canonical version
  const effectiveStamp = resolvedStamp
    ? { ...resolvedStamp, version: alignment.version }
    : { version: alignment.version }

  const args = buildElectronBuilderArgs({
    stamp: effectiveStamp,
    dist,
    extraArgs
  })

  return {
    alignment,
    args,
    effectiveStamp,
    dist
  }
}

export function runBuilder({
  desktopRoot = process.env.HERMES_DESKTOP_ROOT,
  repoRoot = process.env.HERMES_REPO_ROOT,
  allowDrift = process.env.HERMES_DESKTOP_ALLOW_VERSION_DRIFT === "1",
  extraArgs = process.argv.slice(2),
  spawnFn = spawnSync,
  electronBuilderBin = process.env.HERMES_ELECTRON_BUILDER_BIN || electronBuilderCli()
} = {}) {
  let prepared
  try {
    prepared = prepareBuilderArgs({
      desktopRoot,
      repoRoot,
      allowDrift,
      extraArgs
    })
  } catch (err) {
    console.error(err.message)
    process.exit(1)
  }

  const dist = prepared.dist
  if (!dist || !fs.existsSync(distBinary(dist))) {
    console.warn(
      "[run-electron-builder] no local electron dist; electron-builder will fetch " +
        "via @electron/get (electronVersion + ELECTRON_MIRROR)."
    )
  }

  console.log(`[run-electron-builder] running electron-builder with args:`, prepared.args)

  const result = spawnFn(process.execPath, [electronBuilderBin, ...prepared.args], {
    stdio: "inherit"
  })
  if (result.error) {
    console.error(`[run-electron-builder] spawn failed: ${result.error.message}`)
    process.exit(1)
  }
  process.exit(result.status == null ? 1 : result.status)
}

function main() {
  runBuilder()
}

if (isMain(import.meta.url)) {
  main()
}
