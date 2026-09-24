# Design: Desktop Installer Version Integrity & Legacy HKLM Migration

## Context

Verified state on the affected Windows host and in this checkout (2026-09-24):

| Surface | Value | Evidence |
| :--- | :--- | :--- |
| Canonical version | `0.21.3` | `pyproject.toml:5` `version = "0.21.3"`; `hermes_cli/__init__.py:6` |
| Desktop manifest version | `0.17.6` | `apps/desktop/package.json:5` |
| NSIS options | `oneClick: false`, `perMachine: false`, `allowToChangeInstallationDirectory: true`, `include: resources/installer.nsh` | `apps/desktop/package.json` `build.nsis` |
| App GUID | `48ae4bdc-0f8d-5252-af1e-bf7c0a8c3649` | UUIDv5 of `appId com.nousresearch.hermes` (`NsisTarget.js:157`) |
| Legacy HKLM uninstall key | `DisplayVersion 0.21.0`, `UninstallString "C:\Program Files\Hermes\Uninstall Hermes.exe" /allusers` | `reg query HKLM\...\Uninstall\48ae4bdc-...` |
| Legacy HKLM install-info key | `HKLM\Software\48ae4bdc-...\InstallLocation = C:\Program Files\Hermes` | `reg query` |
| HKCU keys | absent (both) | `reg query` → not found |
| Legacy bundled stamp | `version 0.21.0`, `dirty: true`, `source: local`, built 2026-09-04 | `C:\Program Files\Hermes\resources\install-stamp.json` |
| Machine-wide shortcuts | `C:\ProgramData\...\Start Menu\Programs\Hermes.lnk`, `C:\Users\Public\Desktop\Hermes.lnk` → `C:\Program Files\Hermes\Hermes.exe` | `.lnk` payload scan |
| Per-user shortcut | `%APPDATA%\...\Start Menu\Programs\Hermes.lnk` → `apps/desktop/release/win-unpacked` (dev artifact, not an installer product) | `.lnk` payload scan |
| Leaked artifacts | `apps/desktop/release/Hermes-0.17.0-win-x64.exe` next to `Hermes-0.21.3-win-x64-2026-09-23.exe` | `ls apps/desktop/release` |

electron-builder NSIS flow (app-builder-lib templates, read from `node_modules/app-builder-lib/templates/nsis/`):

- `.onInit` (`installer.nsi:77-81`): `initMultiUser` reads `HKLM\Software\${APP_GUID}\InstallLocation` and `HKCU\...` to set `$hasPerMachineInstallation` / `$hasPerUserInstallation` (`assistedInstaller.nsh:107-120`); with only HKLM present it pre-selects `all` mode; then `customInit` runs.
- Install-mode page (`multiUserUi.nsh`): user may still choose "Only for me" → `setInstallModePerUser` sets `$installMode = CurrentUser`, `SetShellVarContext current`.
- Page order (`assistedInstaller.nsh:18-46`): `PAGE_INSTALL_MODE` → `MUI_PAGE_DIRECTORY` → `customPageAfterChangeDir` (optional hook, "after change installation directory and before install start") → `MUI_PAGE_INSTFILES`. Note `MUI_PAGE_CUSTOMFUNCTION_PRE instFilesPre` is defined at line 30 and is consumed by the *next MUI page macro*; a hook page must therefore be declared with raw `Page custom` / `PageEx custom` rather than `MUI_PAGE_CUSTOM`, or it would steal that define.
- Install section (`installSection.nsh:52-58`): `uninstallOldVersion SHELL_CONTEXT`; only when `$installMode == "all"` does it additionally run `uninstallOldVersion HKEY_CURRENT_USER`. There is no HKLM cleanup on a per-user install. `uninstallOldVersion` (`installUtil.nsh:142-243`) copies the old uninstaller to `$PLUGINSDIR\old-uninstaller.exe` and runs it with `/S /KEEP_APP_DATA <mode> --updated _?=<dir>`.
- Registry write (`include/installer.nsh:103-168`): `DisplayVersion` = `${VERSION}` = `appInfo.version`, which is `package.json.version` unless overridden by `-c.extraMetadata.version`.

Build pipeline (`apps/desktop/package.json` scripts): `build` → `write-build-stamp.mjs` (canonical version from `pyproject.toml`, fallback `hermes_cli/__init__.py`; writes `build/install-stamp.json`) → vite/esbuild; `builder` → `run-electron-builder.mjs` → `buildElectronBuilderArgs` adds `-c.extraMetadata.version=<stamp.version>`, `-c.buildVersion`, `-c.artifactName=Hermes-${version}-${os}-${arch}-<date>.${ext}` **only if** the stamp loaded; `afterPack` → `set-exe-identity.mjs` stamps PE `FileVersion`/`ProductVersion` from the stamp (fail-closed per `after-pack.mjs:37`).

Constraints:
- `apps/desktop/AGENTS.md`: scripts must be pure/DI-testable; "when in doubt, read the scripts" — no invented build commands.
- Leader directive (agent_share.md 2026-09-24 10:45): `perMachine` stays `false`; the out-of-scope `perMachine: true` edit was reverted.
- Zero-Trust I/O: the design must not require destructive operations on the developer's real `C:\Program Files\Hermes` during authoring; manual verification is an explicit QA task.

## Goals / Non-Goals

**Goals**
- A per-user installer run on a machine with a legacy HKLM install offers, once, to remove that install with a single UAC prompt, then proceeds per-user; shortcuts no longer resolve to the stale `0.21.0`.
- Declining elevation is a supported path with an explicit, documented outcome (not a silent one).
- No desktop build can produce an artifact whose manifest, stamp and `pyproject.toml` versions disagree.
- All four identity surfaces — About / `app.getVersion()`, uninstall `DisplayVersion`, installer filename, PE `FileVersion` — are provably derived from one value.

**Non-Goals**
- Changing install mode defaults (`perMachine`, `selectPerMachineByDefault`, `oneClick`).
- Modifying electron-builder templates in `node_modules`.
- MSI target parity (`win.target` includes `msi`; WiX has its own upgrade-code semantics — tracked as follow-up, not in this change).
- Runtime version resolution inside Electron (`desktop-runtime-version` capability, change `desktop-packaged-version-resolution`).
- Auto-elevating the whole installer to per-machine mode when a legacy install is found (that is already electron-builder's behavior when the user picks "All users").

## Decisions

### D1: Keep `perMachine: false`; add an opt-in, consent-gated HKLM cleanup instead of forcing per-machine
Rationale: per-machine requires admin for every future upgrade and for every user; per-user is the product default and the Leader directive. The legacy install is a one-time migration problem, so it is solved with a one-time, consent-gated cleanup, not a permanent mode change.

Alternative rejected: `perMachine: true` (reverted by Leader; forces UAC for all installs, changes `$INSTDIR` default, breaks non-admin users).

### D2: Hook point is `customPageAfterChangeDir`, declared as a raw `Page custom` with a pre-function
The hook runs after the user has chosen mode and directory and before any file is written, so a decline or a failed uninstall leaves no half-installed state. It is inserted by electron-builder only in the assisted (non-`oneClick`) installer, which is our configuration.

Implementation shape in `resources/installer.nsh`:

```
!macro customPageAfterChangeDir
  Page custom hermesLegacyHklmPre
!macroend

Function hermesLegacyHklmPre
  ; 1. Only for per-user intent, outer (non-elevated) instance, and non-silent.
  ${If} $installMode != "CurrentUser"
  ${OrIf} ${UAC_IsInnerInstance}
  ${OrIf} ${Silent}
    Abort            ; skip page
  ${EndIf}
  ; 2. Detect legacy machine-wide install.
  ReadRegStr $R0 HKLM "${UNINSTALL_REGISTRY_KEY}" UninstallString
  ${If} $R0 == ""
    Abort
  ${EndIf}
  ReadRegStr $R1 HKLM "${UNINSTALL_REGISTRY_KEY}" DisplayVersion
  ReadRegStr $R2 HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation
  ; 3. Prompt. /SD IDNO makes the default safe if a future silent path reaches here.
  MessageBox MB_YESNOCANCEL|MB_ICONEXCLAMATION|MB_DEFBUTTON1 \
    "A machine-wide Hermes $R1 is installed at $R2.$\r$\n$\r$\nRemove it now (administrator approval required) so shortcuts open the version you are installing?$\r$\n$\r$\nYes = remove (UAC prompt), No = keep it and continue, Cancel = quit installer" \
    /SD IDNO IDYES hermes_remove IDNO hermes_keep
  Quit                                        ; IDCANCEL
hermes_remove:
  ; 4. Copy uninstaller out of its own directory (it deletes that directory), run elevated & silent.
  !insertmacro GetInQuotes $R3 "$R0"
  CopyFiles /SILENT "$R3" "$PLUGINSDIR\legacy-uninstaller.exe"
  ExecShellWait "runas" "$PLUGINSDIR\legacy-uninstaller.exe" '/allusers /S /KEEP_APP_DATA --updated _?=$R2' SW_HIDE
  ; 5. Re-check; on failure fall through to the same keep/quit choice.
  ReadRegStr $R0 HKLM "${UNINSTALL_REGISTRY_KEY}" UninstallString
  ${If} $R0 != ""
    MessageBox MB_YESNO|MB_ICONSTOP "The machine-wide Hermes could not be removed (elevation declined or uninstaller failed).$\r$\n$\r$\nContinue installing for the current user anyway? Existing shortcuts will keep opening the old version." /SD IDNO IDYES hermes_keep
    Quit
  ${EndIf}
hermes_keep:
  Abort            ; no UI page is shown; continue to INSTFILES
FunctionEnd
```

Key properties:
- Uses `ExecShellWait "runas"` on the **legacy uninstaller only** — never `UAC_RunElevated`, which would relaunch the whole installer as an elevated inner instance and turn the run into a per-machine install (`multiUserUi.nsh:32-37`). The single UAC prompt is the one the user consented to.
- Passes `/allusers /S /KEEP_APP_DATA --updated _?=<dir>` — the same argument shape electron-builder itself uses (`installUtil.nsh:224`) so the legacy uninstaller removes HKLM keys, `C:\Program Files\Hermes`, and machine-wide shortcuts while leaving `%APPDATA%\Hermes` intact. `--keep-shortcuts` is deliberately **not** passed: the per-user install writes its own shortcuts.
- Uninstaller exit status is not trusted (`ExecShellWait` with `runas` returns the process exit code but a UAC cancel surfaces as a non-zero shell error). Success is defined by the **HKLM key being gone** on re-read.
- `UNINSTALL_REGISTRY_KEY`, `INSTALL_REGISTRY_KEY`, `$installMode`, `${UAC_IsInnerInstance}`, `GetInQuotes` are all already defined by the electron-builder template that `!include`s `installer.nsh` (`multiUser.nsh:8-12`, `installUtil.nsh`, `UAC.nsh`).
- Registry view is already 64-bit (`check64BitAndSetRegView` in `.onInit`), matching where the legacy key lives.

Alternatives rejected:
- `customInit`: runs before the mode page, so per-user intent is unknown; prompting there would be wrong for users who choose "All users".
- `customInstall` (section time): runs after `uninstallOldVersion SHELL_CONTEXT` and mid-install; a decline would leave the run half-done, and section code cannot cleanly `Quit`.
- Defining `UNINSTALL_REGISTRY_KEY_2`: only affects `uninstallOldVersion` under the *current* root key, not HKLM from a `CurrentUser` run.

### D3: Silent per-user installs (`/currentuser /S`) perform no legacy cleanup
NSIS does not run page callbacks in silent mode, and prompting is impossible. Documented as a spec scenario; a silent installer with a legacy HKLM install is unchanged from today. (Silent installs *without* `/currentuser` already elevate to per-machine via `installer.nsi:99-119` and are unaffected.)

### D4: Drift guard is a pure function in `run-electron-builder.mjs`, fail-closed, with an explicit escape hatch

```
export function assertVersionAlignment({ packageJsonVersion, pyprojectVersion, stampVersion, allowDrift = false })
  → { ok: true, version } | throws Error(message)
```

Rules:
- All three inputs must be present and pass `validateSemVer` (imported from `./write-build-stamp.mjs`); a missing stamp is an error ("run `npm run build` first"), not a warning — this closes the current silent fallback in `loadStampForBuilder() → null`.
- All three must be string-equal after trim. On mismatch the error lists every source and value on one line each, e.g.
  `[run-electron-builder] version drift: package.json=0.17.6 pyproject.toml=0.21.3 install-stamp.json=0.21.3 — align apps/desktop/package.json "version" with pyproject.toml`.
- `allowDrift === true` (wired from `process.env.HERMES_DESKTOP_ALLOW_VERSION_DRIFT === "1"`) converts the throw into a `console.warn` with the same text and returns `{ ok: false, version: pyprojectVersion }`. No other bypass exists.
- New helper `loadCanonicalVersionForBuilder(repoRoot)` reuses `resolveCanonicalVersion` from `write-build-stamp.mjs` (already exported and tested) so `pyproject.toml` parsing is not duplicated. `package.json` is read with `JSON.parse(fs.readFileSync(desktopRoot/package.json))`.
- `main()` calls the guard **before** `buildElectronBuilderArgs` and exits `1` on throw. `buildElectronBuilderArgs` is unchanged; `-c.extraMetadata.version` stays as belt-and-braces.

Alternatives rejected:
- Guard in `write-build-stamp.mjs`: runs during `npm run build`, but `npm run builder` can be invoked alone (see `pack`/`dist` scripts); the packaging step is the last line of defence.
- Auto-rewriting `package.json` at build time: mutates a tracked file, hides the drift, and contradicts the existing "without mutating package.json" contract in `buildElectronBuilderArgs`.

### D5: One-time manifest alignment `apps/desktop/package.json` `version` `0.17.6` → `0.21.3`
Required for the guard to pass on the current tree. This is the only change to `package.json`; no `build.*` keys are touched. Future release bumps update `pyproject.toml` and `apps/desktop/package.json` in the same commit; the guard makes forgetting one a build failure, not a shipped defect.

### D6: Identity surfaces and their single source
| Surface | Derivation | Guarded by |
| :--- | :--- | :--- |
| Installer filename `Hermes-<v>-win-x64-<date>.exe` | `-c.artifactName` with `${version}` = `extraMetadata.version` = stamp | D4 (stamp = pyproject) |
| Uninstall registry `DisplayVersion` | `${VERSION}` = `appInfo.version` = `extraMetadata.version` | D4 |
| `app.getVersion()` / About panel | `package.json` inside `app.asar`, rewritten by `extraMetadata` | D4 + D5 |
| PE `FileVersion` / `ProductVersion` | `set-exe-identity.mjs` from stamp | existing fail-closed afterPack |
| Runtime status bar / `hermes:version` | stamp-first ladder | `desktop-runtime-version` (sibling change) |

## Risks / Trade-offs

- [User with a legacy HKLM install picks "Only for me" and declines] → per-user install proceeds; machine-wide shortcuts still open `0.21.0`. This is now an informed choice with a visible warning, versus today's silent outcome. Mitigation: message text names the consequence.
- [Legacy uninstaller of an older electron-builder ignores `--updated` / `/KEEP_APP_DATA`] → user data in `%APPDATA%\Hermes` could be removed. The legacy build (`0.21.0`, built 2026-09-04 from this repo) uses the same template generation, which honors both flags; `customInstall`'s seed logic also re-creates `connections.json` if missing. Residual risk accepted and listed in QA task 4.3 (backup `%APPDATA%\Hermes` before manual test).
- [`ExecShellWait` unavailable in the bundled makensis] → `ExecShellWait` is a core NSIS 3.x instruction and app-builder-lib 26.15.3 ships NSIS 3.x; if compile fails, the documented fallback is the already-included StdUtils pair `${StdUtils.ExecShellWaitEx} $0 $1 "<exe>" "runas" "<args>"` + `${StdUtils.WaitForProcEx} $0 $1` (`templates/nsis/include/StdUtils.nsh:63-64,299-306`). `warningsAsErrors: false` is already set. Verified at implementation time by compiling (`npm run dist:win:nsis`).
- [Guard breaks CI/local builds immediately] → intended; D5 is part of the same change and lands first in `tasks.md` ordering. The env escape hatch exists for emergency builds and always warns.
- [MSI target still carries `package.json` version pre-`extraMetadata`] → MSI uses the same `appInfo.version`, so it benefits from D4/D5; upgrade-code behavior is out of scope.
- [Testing NSIS logic] → no unit harness for `.nsh`; coverage is (1) `makensis` compile success, (2) the manual acceptance matrix executed by QA on the affected host, (3) `reg query` / `.lnk` evidence captured before and after.

## Migration Plan

1. Coder: D5 manifest bump → D4 guard + tests (`write-build-stamp.test.mjs`) → D2 NSIS hook. Run `cd apps/desktop && npx vitest run scripts/write-build-stamp.test.mjs` and `npm run dist:win:nsis` (compile proof; artifact name must be `Hermes-0.21.3-win-x64-<date>.exe`).
2. Reviewer: diff confined to the four files in proposal "Impact"; NSIS hook uses `ExecShellWait runas` not `UAC_RunElevated`; guard has no silent path.
3. QA (manual, affected host, after backing up `%APPDATA%\Hermes`): execute acceptance matrix rows (a)–(d) from the spec; capture `reg query` of both HKLM/HKCU uninstall keys, `.lnk` targets, About panel, and `Get-Item Hermes.exe | % VersionInfo` before/after.
4. Rollback: revert the commit. The NSIS hook is additive; the guard can be bypassed with `HERMES_DESKTOP_ALLOW_VERSION_DRIFT=1` if a hotfix build is needed before revert. No persisted state on user machines is written by this change beyond what the legacy uninstaller already removes with consent.

## Open Questions

- Should the MSI target (`win.target: ["nsis","msi"]`) be dropped or given equivalent legacy-cleanup semantics? Out of scope; proposed as a follow-up card for @designer once this change is archived.
- Whether to also warn (not block) at `npm run build` time via `write-build-stamp.mjs` for earlier feedback. Not required for integrity; left out to keep the diff minimal.
