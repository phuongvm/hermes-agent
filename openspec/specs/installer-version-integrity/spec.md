# Specification: Installer Version Integrity

## Purpose
Governs Windows NSIS installer version alignment, pre-build drift enforcement across project manifests, legacy machine-wide (HKLM) installation detection and elevated removal, and end-to-end identity consistency for Hermes Desktop.

## Requirements

### Requirement: Per-User Install Mode Is Preserved
The Windows NSIS installer configuration SHALL keep `perMachine: false` and `oneClick: false`. The installer SHALL NOT force, pre-select, or silently switch a run to per-machine mode as a consequence of legacy machine-wide install detection.

#### Scenario: Install mode options are unchanged
- **WHEN** the desktop `build.nsis` configuration is inspected
- **THEN** `perMachine` SHALL be `false`
- **AND** `oneClick` SHALL be `false`
- **AND** `selectPerMachineByDefault` SHALL NOT be set to `true`

#### Scenario: All-users selection keeps existing electron-builder behavior
- **WHEN** the user selects "All users" on the install mode page
- **THEN** the installer SHALL follow the unmodified electron-builder elevation and `uninstallOldVersion` flow
- **AND** the legacy-cleanup hook defined in this specification SHALL NOT run

### Requirement: Legacy Machine-Wide Installation Is Detected Before Files Are Written
When the installer is running non-silently, as the outer (non-elevated) instance, and the selected install mode is `CurrentUser`, it SHALL — after the directory page and before the install-files page — read `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\<APP_GUID>\UninstallString`. A non-empty value SHALL be treated as a legacy machine-wide installation.

#### Scenario: No legacy installation present
- **WHEN** the HKLM uninstall key has no `UninstallString`
- **THEN** no prompt SHALL be shown
- **AND** installation SHALL proceed exactly as before this change

#### Scenario: Legacy installation present on a per-user run
- **WHEN** the HKLM uninstall key has a non-empty `UninstallString`
- **AND** `$installMode` is `CurrentUser`
- **AND** the process is not a UAC inner instance
- **AND** the installer is not silent
- **THEN** the installer SHALL show a prompt naming the legacy `DisplayVersion` and `InstallLocation` before any file is written to `$INSTDIR`
- **AND** the prompt SHALL offer three outcomes: remove (elevate), keep and continue, quit

#### Scenario: Silent per-user install skips legacy cleanup
- **WHEN** the installer runs with `/S` (silent)
- **THEN** the legacy-cleanup hook SHALL NOT run
- **AND** the outcome SHALL be identical to the pre-change silent behavior

### Requirement: Legacy Installation Removal Requires Explicit Consent And A Single Elevation
When the user chooses to remove the legacy installation, the installer SHALL copy the legacy uninstaller out of its installation directory and execute that copy with the `runas` verb (one UAC prompt), passing `/allusers /S /KEEP_APP_DATA --updated _?=<legacy InstallLocation>`. The installer SHALL NOT elevate itself (no `UAC_RunElevated`) and SHALL NOT pass `--delete-app-data`.

#### Scenario: Elevation accepted and legacy uninstaller succeeds
- **WHEN** the user chooses remove
- **AND** the UAC prompt is approved
- **AND** the legacy uninstaller completes
- **THEN** `HKLM\...\Uninstall\<APP_GUID>` SHALL no longer exist
- **AND** `HKLM\Software\<APP_GUID>` SHALL no longer exist
- **AND** `C:\Program Files\Hermes\Hermes.exe` SHALL no longer exist
- **AND** machine-wide Start Menu and Public Desktop `Hermes.lnk` SHALL no longer exist
- **AND** `%APPDATA%\Hermes\connections.json` SHALL still exist if it existed before
- **AND** the installer SHALL continue to the install-files page in `CurrentUser` mode with `$INSTDIR` under `%LOCALAPPDATA%\Programs`

#### Scenario: Success is judged by registry state, not exit code
- **WHEN** the legacy uninstaller returns
- **THEN** the installer SHALL re-read `HKLM\...\Uninstall\<APP_GUID>\UninstallString`
- **AND** removal SHALL be considered successful only if that value is now empty

### Requirement: Declined Or Failed Elevation Has An Explicit, Non-Destructive Outcome
If the UAC prompt is declined, or the legacy uninstaller fails, the HKLM key will still be present on re-read. The installer SHALL then show a second prompt stating that existing shortcuts will keep opening the old version and offering to continue per-user or quit. Choosing quit SHALL exit the installer with no files written and no registry changes.

#### Scenario: Elevation declined, user continues per-user
- **WHEN** the UAC prompt is declined
- **AND** the user chooses to continue
- **THEN** the per-user installation SHALL complete under `%LOCALAPPDATA%\Programs\Hermes`
- **AND** `HKCU\...\Uninstall\<APP_GUID>\DisplayVersion` SHALL equal the new version
- **AND** `HKLM\...\Uninstall\<APP_GUID>\DisplayVersion` SHALL be unchanged (legacy version)
- **AND** machine-wide shortcuts SHALL be unchanged

#### Scenario: Elevation declined, user quits
- **WHEN** the UAC prompt is declined
- **AND** the user chooses quit
- **THEN** the installer SHALL exit
- **AND** neither `HKCU\...\Uninstall\<APP_GUID>` nor `%LOCALAPPDATA%\Programs\Hermes\Hermes.exe` SHALL have been created by this run

#### Scenario: User keeps legacy install at the first prompt
- **WHEN** the user chooses keep at the first prompt
- **THEN** no elevation SHALL be requested
- **AND** the per-user installation SHALL proceed as in the "elevation declined, user continues" scenario

### Requirement: Desktop Build Refuses To Package On Version Drift
Before `electron-builder` is spawned, the desktop packaging script SHALL assert that `apps/desktop/package.json` `version`, the canonical version from `pyproject.toml` (`[project].version`, fallback `hermes_cli/__init__.py` `__version__`), and `apps/desktop/build/install-stamp.json` `version` are all present, all valid SemVer, and all string-equal. On any violation the script SHALL exit non-zero without invoking `electron-builder` and SHALL print every source with its value plus the remediation. Setting `HERMES_DESKTOP_ALLOW_VERSION_DRIFT=1` SHALL downgrade the failure to a warning that prints the same text; no other bypass SHALL exist.

#### Scenario: All three versions agree
- **WHEN** `package.json`, `pyproject.toml` and `install-stamp.json` all declare `0.21.3`
- **THEN** the guard SHALL pass
- **AND** `electron-builder` SHALL be invoked with `-c.extraMetadata.version=0.21.3`

#### Scenario: Manifest drifts from canonical
- **WHEN** `package.json` declares `0.17.6` and `pyproject.toml` and `install-stamp.json` declare `0.21.3`
- **THEN** the guard SHALL fail with a message containing `package.json=0.17.6`, `pyproject.toml=0.21.3`, `install-stamp.json=0.21.3`
- **AND** the process exit code SHALL be non-zero
- **AND** `electron-builder` SHALL NOT be spawned

#### Scenario: Stamp is missing
- **WHEN** `apps/desktop/build/install-stamp.json` does not exist or cannot be parsed
- **THEN** the guard SHALL fail and instruct the operator to run the desktop `build` script first
- **AND** `electron-builder` SHALL NOT be spawned

#### Scenario: Stamp version is not valid SemVer
- **WHEN** `install-stamp.json` `version` fails `validateSemVer`
- **THEN** the guard SHALL fail naming the invalid value

#### Scenario: Escape hatch warns but does not block
- **WHEN** versions disagree
- **AND** `HERMES_DESKTOP_ALLOW_VERSION_DRIFT` equals `1`
- **THEN** the guard SHALL print the same drift message as a warning
- **AND** `electron-builder` SHALL be invoked
- **AND** `-c.extraMetadata.version` SHALL equal the `pyproject.toml` version

#### Scenario: Guard is a pure, unit-tested function
- **WHEN** `assertVersionAlignment` is called with explicit `packageJsonVersion`, `pyprojectVersion`, `stampVersion`, `allowDrift` inputs
- **THEN** its result SHALL depend only on those inputs
- **AND** it SHALL NOT read the filesystem or environment

### Requirement: Desktop Manifest Version Matches Canonical Version
`apps/desktop/package.json` `version` SHALL equal `pyproject.toml` `[project].version` on every commit that produces a release build. This change aligns the current tree to `0.21.3`.

#### Scenario: Current tree is aligned
- **WHEN** this change is applied
- **THEN** `apps/desktop/package.json` `version` SHALL be `0.21.3`
- **AND** no other field of `apps/desktop/package.json` SHALL be modified by this change

### Requirement: Every Version-Bearing Surface Of The Installed Product Matches Canonical
For a build produced with the drift guard passing, the installer filename, the uninstall registry `DisplayVersion`, `app.getVersion()` / the About panel, and the PE `FileVersion` of `Hermes.exe` SHALL all equal the `pyproject.toml` version.

#### Scenario: End-to-end identity check
- **WHEN** an installer built from a tree whose canonical version is `V` is run and the app is launched
- **THEN** the installer filename SHALL match `Hermes-V-win-x64-<YYYY-MM-DD>.exe`
- **AND** `HKCU\...\Uninstall\<APP_GUID>\DisplayVersion` SHALL equal `V`
- **AND** the About panel and `app.getVersion()` SHALL equal `V`
- **AND** `Hermes.exe` PE `FileVersion` SHALL begin with `V`
- **AND** `resources\install-stamp.json` `version` SHALL equal `V`

### Requirement: Acceptance Matrix
The following matrix SHALL be executed on a Windows host and its evidence attached before this change is archived. Rows (a)–(c) require a legacy machine-wide install fixture; row (d) applies to every row's resulting install. Evidence per row: `reg query` of `HKLM` and `HKCU` uninstall keys, `.lnk` target paths, About panel text, `Hermes.exe` `VersionInfo.FileVersion`, and the installer filename.

| Row | Precondition | Action | Expected |
| :---: | :--- | :--- | :--- |
| (a) | No `HKLM\...\Uninstall\<APP_GUID>`; no `HKCU\...\Uninstall\<APP_GUID>` | Run installer, choose "Only for me", accept defaults | No legacy prompt shown. Install under `%LOCALAPPDATA%\Programs\Hermes`. `HKCU` uninstall `DisplayVersion` = `V`. Per-user Start Menu shortcut → new `Hermes.exe`. `HKLM` key still absent. |
| (b) | `HKLM\...\Uninstall\<APP_GUID>` present (`DisplayVersion` older than `V`), `C:\Program Files\Hermes\Hermes.exe` present, machine-wide shortcuts present | Run installer, choose "Only for me", at legacy prompt choose remove, approve UAC | Exactly one UAC prompt. After completion: `HKLM` uninstall key and `HKLM\Software\<APP_GUID>` absent; `C:\Program Files\Hermes` removed; machine-wide `Hermes.lnk` files removed; `%APPDATA%\Hermes\connections.json` preserved; per-user install present with `HKCU` `DisplayVersion` = `V`; per-user shortcut → new `Hermes.exe`. |
| (c) | Same as (b) | Run installer, choose "Only for me", at legacy prompt choose remove, decline UAC, at second prompt choose continue | `HKLM` key, `C:\Program Files\Hermes`, and machine-wide shortcuts unchanged; per-user install present with `HKCU` `DisplayVersion` = `V`; second prompt text warned that existing shortcuts keep opening the old version. Variant (c'): choose quit at second prompt → no `HKCU` key, no `%LOCALAPPDATA%\Programs\Hermes\Hermes.exe` created. |
| (d) | Any resulting install from (a), (b) or (c) | Launch app; open About; run `(Get-Item "<install dir>\Hermes.exe").VersionInfo.FileVersion`; read `HKCU` `DisplayVersion`; read installer filename | All four equal `V` (= `pyproject.toml` version). |

#### Scenario: Row (a) — clean per-user install
- **WHEN** row (a) is executed
- **THEN** every "Expected" item in row (a) SHALL hold

#### Scenario: Row (b) — stale HKLM install, elevation accepted
- **WHEN** row (b) is executed
- **THEN** every "Expected" item in row (b) SHALL hold

#### Scenario: Row (c) — stale HKLM install, elevation declined
- **WHEN** row (c) is executed
- **THEN** every "Expected" item in row (c) SHALL hold

#### Scenario: Row (d) — identity surfaces match canonical
- **WHEN** row (d) is executed against the install produced by rows (a), (b) and (c)
- **THEN** every "Expected" item in row (d) SHALL hold

### Requirement: Change Does Not Alter Sibling Contracts
This change SHALL NOT modify electron-builder templates under `node_modules`, `write-build-stamp.mjs`, `set-exe-identity.mjs`, `after-pack.mjs`, any file under `apps/desktop/electron/`, or the `customInstall` gateway-seed logic already present in `resources/installer.nsh`.

#### Scenario: Diff scope is confined
- **WHEN** the implementation diff is inspected
- **THEN** only `apps/desktop/resources/installer.nsh`, `apps/desktop/scripts/run-electron-builder.mjs`, `apps/desktop/scripts/write-build-stamp.test.mjs`, and the `version` field of `apps/desktop/package.json` SHALL have changed

#### Scenario: Existing build-script tests remain green
- **WHEN** `write-build-stamp.test.mjs` is executed after the change
