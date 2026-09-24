# Change: Packaged Runtime Version Resolution (Bundled Stamp First)

## Why

When Hermes Desktop is installed from the Windows installer (e.g. `Hermes-0.21.3-win-x64-*.exe`) on a machine that also has a local Hermes Python runtime under `%LOCALAPPDATA%\hermes\hermes-agent`, the Remote Mode status bar and About panel display the local runtime's version (`client v0.20.0`) instead of the packaged client's version (`0.21.3`). The desktop binary is misreporting its own identity, which defeats update prompts, support triage, and renderer-skew detection.

Root cause (evidence): `resolveHermesVersionLadder` in `apps/desktop/electron/runtime-version.ts:174-209` evaluates Rung 1 (`ctx.updateRoot` → `hermes_cli/__init__.py`) unconditionally before Rung 2 (bundled `install-stamp.json`). `main.ts::resolveHermesVersion()` (`apps/desktop/electron/main.ts:17644-17651`) always passes `updateRoot: resolveUpdateRoot()`, and `resolveUpdateRoot()` (`main.ts:3092-3100`) resolves to `ACTIVE_HERMES_ROOT` in packaged builds. Rung 1 therefore intercepts whenever any Python source tree exists on disk, regardless of whether the running binary was built from it.

## What Changes

- Add an `isPackaged: boolean` input to the version resolution context so the ladder knows which authority governs the running process.
- Reorder the resolution ladder by packaging mode:
  - Packaged (`isPackaged === true`): bundled install stamp is consulted FIRST. A stamp with a valid `version` and `shortCommit` is authoritative and returns `formatClientVersion(stamp)`. Only when the stamp is absent or invalid does resolution fall back to the local source tree (`updateRoot`) and then `appVersion`.
  - Development (`isPackaged === false` / unset): current order is preserved — local source tree first, then stamp, then `appVersion`.
- Wire `isPackaged: IS_PACKAGED` at the single production call site `main.ts::resolveHermesVersion()`.
- Add regression tests for the packaged-first truth table in `apps/desktop/electron/runtime-version.test.ts` (pure/DI-tested; no source-reading tests).
- No change to the IPC response shape (`hermes:version`), the renderer, `resolveUpdateRoot()`, or bundle-skew detection.

## Capabilities

### New Capabilities
- `desktop-runtime-version`: Defines how the Electron main process resolves the canonical client version string exposed to the About panel, `hermes:version` IPC, and status bar, including the packaging-mode-dependent authority order between the bundled install stamp, a local Hermes source tree, and the Electron app version.

### Modified Capabilities
- (none — `desktop-reconnect-resilience`, `desktop-remote-file-preview`, and other existing specs do not govern version resolution)

## Impact

- Code: `apps/desktop/electron/runtime-version.ts` (context type + ladder ordering), `apps/desktop/electron/main.ts` (one call site: `resolveHermesVersion()`), `apps/desktop/electron/runtime-version.test.ts` (new scenarios).
- Consumers (unchanged contract, corrected value): `ipcMain.handle('hermes:version')` (`main.ts:17683`), `showAboutPanelFresh()` (`main.ts:17670`), renderer `store/updates.ts:369` → `use-statusbar-items.tsx:359` → `version-status.ts:83-87` → `i18n/en.ts:3682` (`client v${version}`), `about-settings.tsx:107`.
- Behavior on a clean packaged install (no local Python tree): unchanged — stamp was already reached because Rung 1 found no `__init__.py`.
- Behavior in dev (`npm start` from checkout): unchanged — source tree stays first.
- Risk surface: low. Pure function reorder gated by an explicit boolean; existing 14 ladder/stamp tests must continue to pass.
