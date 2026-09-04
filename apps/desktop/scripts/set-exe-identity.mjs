#!/usr/bin/env node
// set-exe-identity.mjs — stamp the Hermes icon + version metadata onto the
// built Hermes.exe using rcedit, completely decoupled from electron-builder's
// signing path.
//
// WHY THIS EXISTS
// ---------------
// apps/desktop/package.json sets build.win.signAndEditExecutable=false. That
// flag is load-bearing: turning electron-builder's own exe-editing ON also
// re-enables its signtool step, which fetches winCodeSign-2.6.0.7z, whose
// macOS symlinks crash 7-Zip on non-admin Windows (no Developer Mode = no
// SeCreateSymbolicLinkPrivilege). That is an unfixable dead end — we do NOT
// try to extract winCodeSign.
//
// The cost of disabling signAndEditExecutable is that electron-builder also
// skips rcedit, so the unpacked Hermes.exe keeps the stock Electron icon and
// "Electron" taskbar name. This script restores the icon + identity by calling
// rcedit DIRECTLY. rcedit is a pure PE resource editor: no signing, no certs,
// no winCodeSign, no symlinks.
//
// HOW IT RUNS
// -----------
// Primarily as an electron-builder `afterPack` hook (scripts/after-pack.mjs),
// so EVERY packed build — first install, `hermes desktop`, the installer's
// --update rebuild, or a dev's manual `npm run pack` — gets a branded exe from
// one place.
//
// Fail-closed policy:
// stampExeIdentity() throws/rejects on failure. after-pack.mjs awaits this
// without swallowing, ensuring packaging terminates with a non-zero exit code
// if version or icon stamping fails.

import { resolve, join } from "node:path"
import { existsSync, readFileSync } from "node:fs"

import { isMain } from "./utils.mjs"

/**
 * Pure helper to construct rcedit options given an install stamp and icon path.
 * Enforces 16-bit integer boundary constraints across all 4 PE tuple components.
 */
export function buildRceditOptions(stamp, iconPath) {
  const options = {
    icon: iconPath,
    "version-string": {
      ProductName: "Hermes",
      FileDescription: "Hermes",
      CompanyName: "Nous Research",
      LegalCopyright: "Copyright (c) 2026 Nous Research"
    }
  }

  if (!stamp) {
    return options
  }

  const canonicalVersion = stamp.version
  if (canonicalVersion) {
    const match = canonicalVersion.match(/^(\d+)\.(\d+)\.(\d+)/)
    if (!match) {
      throw new Error(`Invalid SemVer version in install stamp: "${canonicalVersion}"`)
    }

    const major = parseInt(match[1], 10)
    const minor = parseInt(match[2], 10)
    const patch = parseInt(match[3], 10)

    if (major < 0 || major > 65535 || minor < 0 || minor > 65535 || patch < 0 || patch > 65535) {
      throw new Error(
        `SemVer component out of 16-bit range [0..65535]: major=${major}, minor=${minor}, patch=${patch}`
      )
    }

    const rawBuild = typeof stamp.buildNumber === "number" && !isNaN(stamp.buildNumber) && stamp.buildNumber >= 0
      ? stamp.buildNumber
      : 0
    const peBuildNumber = rawBuild % 65536
    const peVersionTuple = `${major}.${minor}.${patch}.${peBuildNumber}`

    options["file-version"] = peVersionTuple
    options["product-version"] = peVersionTuple

    const shortCommit = stamp.shortCommit || (stamp.commit ? stamp.commit.slice(0, 8) : null)
    options["version-string"].ProductVersion = shortCommit
      ? `${canonicalVersion} (${shortCommit})`
      : canonicalVersion
    options["version-string"].FileVersion = canonicalVersion
  }

  return options
}

// Stamp the Hermes icon + identity onto `exe`. Resolves on success, throws on
// failure. `desktopRoot` defaults to this script's package root so the icon and
// the rcedit dependency resolve regardless of cwd.
async function stampExeIdentity(exe, desktopRoot = resolve(import.meta.dirname, "..")) {
  if (!exe || !existsSync(exe)) {
    throw new Error(`target exe not found: ${exe}`)
  }

  // Icon lives at apps/desktop/assets/icon.ico
  const icon = join(desktopRoot, "assets", "icon.ico")
  if (!existsSync(icon)) {
    throw new Error(`icon not found: ${icon}`)
  }

  // Load install stamp if present
  const stampPath = join(desktopRoot, "build", "install-stamp.json")
  let stamp = null
  if (existsSync(stampPath)) {
    try {
      const raw = readFileSync(stampPath, "utf8")
      stamp = JSON.parse(raw)
    } catch (err) {
      throw new Error(`failed to parse install stamp at ${stampPath}: ${err.message}`)
    }
  } else {
    throw new Error(`install stamp not found at ${stampPath}; cannot stamp PE identity`)
  }

  console.log(`[set-exe-identity] stamping ${exe}`)
  console.log(`[set-exe-identity] icon: ${icon}`)

  const rceditOpts = buildRceditOptions(stamp, icon)
  let rceditFn
  try {
    const rceditModule = await import("rcedit")
    rceditFn = rceditModule.rcedit || rceditModule.default || rceditModule
  } catch (err) {
    throw new Error(`rcedit dependency could not be loaded: ${err.message}`)
  }

  await rceditFn(exe, rceditOpts)

  console.log("[set-exe-identity] done — Hermes icon + identity stamped")
}

export { stampExeIdentity }

// CLI entry point: `node scripts/set-exe-identity.mjs <exe>`.
if (isMain(import.meta.url)) {
  const exe = process.argv[2]
  if (!exe) {
    console.error("[set-exe-identity] usage: set-exe-identity.mjs <path-to-exe>")
    process.exit(2)
  }
  stampExeIdentity(exe).catch(err => {
    console.error(`[set-exe-identity] ${err.message}`)
    process.exit(1)
  })
}
