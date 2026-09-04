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

const require = createRequire(import.meta.url)

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

    args.push(`-c.extraMetadata.version=${canonicalVersion}`)
    args.push(`-c.buildVersion=${canonicalVersion}.${peBuildNumber}`)
    args.push(`-c.buildNumber=${peBuildNumber}`)
  }

  args.push(...extraArgs)
  return args
}

export function loadStampForBuilder(desktopRoot = path.resolve(import.meta.dirname, "..")) {
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

function main() {
  const dist = electronDistDir()
  if (!dist || !fs.existsSync(distBinary(dist))) {
    console.warn(
      "[run-electron-builder] no local electron dist; electron-builder will fetch " +
        "via @electron/get (electronVersion + ELECTRON_MIRROR)."
    )
  }

  const stamp = loadStampForBuilder()
  const args = buildElectronBuilderArgs({
    stamp,
    dist,
    extraArgs: process.argv.slice(2)
  })

  console.log(`[run-electron-builder] running electron-builder with args:`, args)

  const result = spawnSync(process.execPath, [electronBuilderCli(), ...args], {
    stdio: "inherit"
  })
  if (result.error) {
    console.error(`[run-electron-builder] spawn failed: ${result.error.message}`)
    process.exit(1)
  }
  process.exit(result.status == null ? 1 : result.status)
}

if (isMain(import.meta.url)) {
  main()
}
