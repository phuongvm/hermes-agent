# Change: Desktop Installer Version Integrity & Legacy HKLM Migration

## Why

Two independent defects let an installed Hermes Desktop on Windows report or launch a version other than the one the release was cut from:

1. **Legacy machine-wide (HKLM) collision.** A stale per-machine install exists at `C:\Program Files\Hermes` (verified 2026-09-24 on the affected host: `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\48ae4bdc-0f8d-5252-af1e-bf7c0a8c3649` → `DisplayVersion 0.21.0`, `UninstallString "C:\Program Files\Hermes\Uninstall Hermes.exe" /allusers`; `HKLM\Software\48ae4bdc-0f8d-5252-af1e-bf7c0a8c3649\InstallLocation = C:\Program Files\Hermes`; bundled `resources\install-stamp.json` = `0.21.0` dirty; `C:\ProgramData\...\Start Menu\Programs\Hermes.lnk` and `C:\Users\Public\Desktop\Hermes.lnk` both target `C:\Program Files\Hermes\Hermes.exe`). The shipped NSIS config is `perMachine: false, oneClick: false` (`apps/desktop/package.json` `build.nsis`). electron-builder's `installSection.nsh:52-58` only uninstalls the previous install under `SHELL_CONTEXT`, and additionally under `HKEY_CURRENT_USER` when `$installMode == "all"`. When the user picks "Only for me" the HKLM install is never touched, so machine-wide shortcuts keep launching `0.21.0` while the new per-user `0.21.3` sits unused in `%LOCALAPPDATA%\Programs\Hermes`.

2. **Package version drift.** `apps/desktop/package.json` declares `"version": "0.17.6"` while the canonical `pyproject.toml` `[project].version` is `0.21.3` (and `hermes_cli/__init__.py` `__version__ = "0.21.3"`). `scripts/run-electron-builder.mjs::buildElectronBuilderArgs` papers over this at build time via `-c.extraMetadata.version=<stamp.version>`, but only when `build/install-stamp.json` is present and readable (`loadStampForBuilder` returns `null` on any failure and the builder silently proceeds with `0.17.6`). `apps/desktop/release/Hermes-0.17.0-win-x64.exe` is a surviving artifact of exactly that leak. Nothing asserts the manifest, the stamp and `pyproject.toml` agree before a build starts.

## What Changes

- **Keep `perMachine: false`.** Standard per-user installation without admin rights remains the default and the only supported mode change is none (decision recorded in design D1).
- **`apps/desktop/resources/installer.nsh`: legacy HKLM detection + elevated cleanup.** Add a `customPageAfterChangeDir` hook (electron-builder assisted-installer seam that runs after the directory page and before `MUI_PAGE_INSTFILES`) whose page-pre function, when the chosen install mode is `CurrentUser` and the process is the outer (non-elevated) instance, reads `HKLM\${UNINSTALL_REGISTRY_KEY}\UninstallString`. If present it prompts the user to remove the machine-wide install; on consent it runs a temp copy of the legacy uninstaller through `ExecShellWait "runas"` (single UAC prompt, silent uninstall with `/allusers /S /KEEP_APP_DATA --updated _?=<legacy dir>`), re-checks the HKLM key, and on decline/failure offers to continue per-user anyway or quit. The existing `customInstall` seed logic is unchanged.
- **`apps/desktop/scripts/run-electron-builder.mjs`: pre-build drift guard.** Add a pure `assertVersionAlignment({ packageJsonVersion, pyprojectVersion, stampVersion })` helper invoked from `main()` before `electron-builder` is spawned. Any mismatch (or missing stamp / unreadable `pyproject.toml`) exits non-zero with a single actionable line. An explicit `HERMES_DESKTOP_ALLOW_VERSION_DRIFT=1` escape hatch downgrades the failure to a loud warning; there is no silent fallback.
- **One-time alignment of `apps/desktop/package.json` `version`** to the canonical `pyproject.toml` value (`0.21.3`) so the guard passes on the current tree. Future bumps must update both files together (the guard enforces it).
- **Explicit acceptance matrix** in the new capability spec covering (a) clean per-user install, (b) stale HKLM + elevation accepted, (c) stale HKLM + elevation declined, (d) end-to-end identity: About panel, `app.getVersion()`, uninstall registry `DisplayVersion`, installer artifact filename and PE `FileVersion` all equal the `pyproject.toml` version.
- Unit tests for `assertVersionAlignment` in `apps/desktop/scripts/write-build-stamp.test.mjs` (the existing home of `buildElectronBuilderArgs` tests).

## Capabilities

### New Capabilities
- `installer-version-integrity`: Defines (1) how the Windows NSIS installer detects and, with user consent and a single elevation, removes a legacy machine-wide (HKLM) Hermes installation before performing a per-user install, and (2) the build-time invariant that the desktop manifest version, the bundled install stamp and the canonical Python project version agree before `electron-builder` runs, so every version-bearing surface of the installed product matches `pyproject.toml`.

### Modified Capabilities
- (none — `desktop-runtime-version` governs runtime resolution of the version string inside the Electron main process and is not touched; this change governs build-time inputs and installer behavior)

## Impact

- Code: `apps/desktop/resources/installer.nsh` (new `customPageAfterChangeDir` macro + page-pre function), `apps/desktop/scripts/run-electron-builder.mjs` (new exported `assertVersionAlignment`, `loadCanonicalVersionForBuilder`, call in `main()`), `apps/desktop/package.json` (`version` field only), `apps/desktop/scripts/write-build-stamp.test.mjs` (new cases).
- Not touched: electron-builder NSIS templates under `node_modules`, `build.nsis` options other than none, `write-build-stamp.mjs`, `set-exe-identity.mjs`, `after-pack.mjs`, `apps/desktop/electron/**`, MSI target behavior (`win.target` also lists `msi`; MSI is out of scope and noted as a follow-up).
- Behavior on a clean machine (no HKLM key): unchanged — the new page-pre function finds no `UninstallString` and skips itself.
- Behavior when the user picks "All users": unchanged — electron-builder's own `uninstallOldVersion HKLM` + elevation path already handles it; the new hook is a no-op because `$installMode != "CurrentUser"`.
- Build behavior: `npm run dist:*` now fails fast with a clear message if `package.json`, `build/install-stamp.json` and `pyproject.toml` disagree. Until `package.json` is aligned (task 3.1) every desktop build on this tree will fail by design.
- Risk surface: low-medium. The NSIS hook is additive and guarded by three conditions (`CurrentUser` mode, outer instance, HKLM key present); the drift guard is a pure function with tests; the manifest bump is a one-field edit.
