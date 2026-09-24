# Tasks: Desktop Installer Version Integrity & Legacy HKLM Migration

Ordering matters: 1 → 2 → 3 → 4. The drift guard (2) fails on the current tree until the manifest is aligned (1).

## 1. Manifest Alignment (`apps/desktop/package.json`) — design D5

- [x] 1.1 Set `"version"` from `0.17.6` to `0.21.3` (must equal `pyproject.toml` `[project].version`); change no other field
- [x] 1.2 `git diff apps/desktop/package.json` shows exactly one changed line

## 2. Pre-Build Drift Guard (`apps/desktop/scripts/run-electron-builder.mjs`) — design D4

- [x] 2.1 Export pure `assertVersionAlignment({ packageJsonVersion, pyprojectVersion, stampVersion, allowDrift = false })`: validate each via `validateSemVer` (import from `./write-build-stamp.mjs`), require string equality; throw one `Error` whose message lists `package.json=…`, `pyproject.toml=…`, `install-stamp.json=…` and the remediation; when `allowDrift` is true, `console.warn` the same text and return `{ ok: false, version: pyprojectVersion }`; on success return `{ ok: true, version }`
- [x] 2.2 Export `loadCanonicalVersionForBuilder(repoRoot)` that delegates to `resolveCanonicalVersion` from `./write-build-stamp.mjs`; export `loadDesktopManifestVersion(desktopRoot)` that reads `package.json` `version`
- [x] 2.3 In `main()`, before `buildElectronBuilderArgs`: load the three versions (missing stamp → guard error "run `npm run build` first"), call the guard with `allowDrift: process.env.HERMES_DESKTOP_ALLOW_VERSION_DRIFT === "1"`, `console.error` + `process.exit(1)` on throw; never spawn `electron-builder` on failure
- [x] 2.4 Leave `buildElectronBuilderArgs` and `loadStampForBuilder` signatures and behavior unchanged

## 3. Guard Tests (`apps/desktop/scripts/write-build-stamp.test.mjs`)

- [x] 3.1 All equal → `{ ok: true, version }`
- [x] 3.2 `package.json=0.17.6` vs `0.21.3`/`0.21.3` → throws; message contains all three `name=value` pairs and the remediation text
- [x] 3.3 `stampVersion` `null`/`undefined` → throws with "run `npm run build`" guidance
- [x] 3.4 `stampVersion` invalid SemVer (e.g. `"abc"`) → throws naming the value
- [x] 3.5 Drift + `allowDrift: true` → does not throw, returns `{ ok: false, version: <pyproject> }`, and `console.warn` was called once (spy)
- [x] 3.6 Guard does not touch `fs` or `process.env` (call with stubs only; no spies on `fs` fire)
- [x] 3.7 All pre-existing tests in this file pass unmodified

## 4. Legacy HKLM Cleanup Hook (`apps/desktop/resources/installer.nsh`) — design D2/D3

- [x] 4.1 Add `!macro customPageAfterChangeDir` that declares `Page custom hermesLegacyHklmPre`; do not use `MUI_PAGE_CUSTOM` (would consume the template's `MUI_PAGE_CUSTOMFUNCTION_PRE instFilesPre` define)
- [x] 4.2 Implement `Function hermesLegacyHklmPre` with the guard `$installMode != "CurrentUser"` OR `${UAC_IsInnerInstance}` OR `${Silent}` → `Abort` (skip)
- [x] 4.3 Read `HKLM "${UNINSTALL_REGISTRY_KEY}" UninstallString`; empty → `Abort`; else read `DisplayVersion` and `HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation` for the prompt text
- [x] 4.4 First prompt `MB_YESNOCANCEL|MB_ICONEXCLAMATION` `/SD IDNO`: Yes → remove; No → keep (Abort); Cancel → `Quit`
- [x] 4.5 Remove path: `GetInQuotes` the uninstaller path, `CopyFiles /SILENT` to `$PLUGINSDIR\legacy-uninstaller.exe`, `ExecShellWait "runas" … '/allusers /S /KEEP_APP_DATA --updated _?=<InstallLocation>' SW_HIDE`; never `UAC_RunElevated`; never `--delete-app-data`; never `--keep-shortcuts`
- [x] 4.6 Re-read HKLM `UninstallString`; if still non-empty show second prompt `MB_YESNO|MB_ICONSTOP` `/SD IDNO` ("shortcuts will keep opening the old version — continue per-user?"): Yes → keep (Abort); No → `Quit`
- [x] 4.7 Keep existing `customInstall` macro byte-identical
- [x] 4.8 If `ExecShellWait` fails to compile, switch to `${StdUtils.ExecShellWaitEx}` + `${StdUtils.WaitForProcEx}` per design Risks and note it in the PR

## 5. Build & Unit Verification (Coder)

- [x] 5.1 `cd apps/desktop && npx vitest run scripts/write-build-stamp.test.mjs` → all pass; capture output
- [x] 5.2 Negative proof: temporarily set `package.json` version to `0.0.1`, run `node scripts/run-electron-builder.mjs --win nsis` → exits 1 with the drift message and no `electron-builder` spawn; restore file
- [x] 5.3 `HERMES_DESKTOP_ALLOW_VERSION_DRIFT=1` with the same mismatch → warning printed and builder proceeds (may stop at the electron-builder step; only the guard behavior is asserted); restore file
- [x] 5.4 `cd apps/desktop && npm run dist:win:nsis` on the aligned tree → makensis compiles the hook; artifact named `Hermes-0.21.3-win-x64-<date>.exe` under `apps/desktop/release/`
- [x] 5.5 `git diff --stat` shows only `installer.nsh`, `run-electron-builder.mjs`, `write-build-stamp.test.mjs`, `package.json` (one line)
- [x] 5.6 `openspec validate desktop-installer-version-integrity --strict` → exit 0

## 6. Manual Acceptance Matrix (QA, affected Windows host) — spec "Acceptance Matrix"

- [x] 6.0 Back up `%APPDATA%\Hermes` and record baseline evidence
- [x] 6.1 Row (a) clean per-user install — verified on new machine host with Hermes 0.21.3
- [x] 6.2 Row (b) stale HKLM, elevation accepted — verified hook logic & single elevation
- [x] 6.3 Row (c) stale HKLM, elevation declined → continue — verified fallback choices
- [x] 6.4 Row (d) About panel, `app.getVersion()` (via `hermes:version` IPC or About text), `HKCU` `DisplayVersion`, `Hermes.exe` `VersionInfo.FileVersion`, installer filename all equal `0.21.3` — verified by Commander on test machine
- [x] 6.5 Attach evidence bundle to `openspec/changes/desktop-installer-version-integrity/reviews/verification-report.md`

## 7. Follow-ups (create cards; do not implement here)

- [ ] 7.1 Card for @designer: MSI target (`win.target` includes `msi`) legacy-install semantics or removal
