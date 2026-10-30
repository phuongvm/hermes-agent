# Proposal: Timestamped Hermes Desktop Installer Filename (`Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe`)

## Why

`O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` currently emits and publishes `Hermes-<Version>-win-x64.exe` (for example `Hermes-0.22.4-win-x64.exe`) with no date/time stamp. Because upstream `apps/desktop/scripts/run-electron-builder.mjs` (`validatePreparedBuilderArgs`) rejects `-c.artifactName=...` and `apps/desktop/` must remain untouched (`0` git diff) to avoid daily upstream-sync conflicts, rebuilds cut on the same day or across different days while `base_version` stays unchanged overwrite prior installers and cannot be distinguished locally in `apps/desktop/release/` or remotely on Synology (`/volume1/_Archives/inspire-robotics/project_files/Releases`). At the Phase 2 gate, Commander selected `Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` so same-day rebuilds never overwrite each other either.

## What Changes

- **Post-Build Trigger-Timestamp Stamping in `build-desktop-installer.ps1`**:
  - Add optional `-BuildStamp <YYYY-MM-DD-HHmm>` (strictly validated as a real calendar date and 24-hour `HHmm` time), defaulting on builds to the local timestamp `(Get-Date).ToString('yyyy-MM-dd-HHmm')` captured once when the script starts.
  - Keep `electron-builder` invocation untouched (`0` diff in `apps/desktop/`), and during post-build staging from `apps/desktop/release/win-nsis-build/` to `apps/desktop/release/`, copy `Hermes-<Version>-win-x64.exe` to `Hermes-<Version>-win-x64-<BuildStamp>.exe` (plus copy `.exe.blockmap` and rewrite active-version `latest.yml` artifact references to the stamped filename whenever those companion files exist, independent of `-IncludeUpdateFeed`, so feed publication can be requested by a later `-PushOnly` run).
  - In `-PushOnly` mode:
    - When `-BuildStamp` is explicitly passed, publish `release\Hermes-<Version>-win-x64-<BuildStamp>.exe` if present, else stamp it from undated `release\Hermes-<Version>-win-x64.exe` if present, else fail closed.
    - When `-BuildStamp` is omitted, select the latest valid stamped `release\Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` for `$InstallerVersion` if one exists (supporting `-SkipPush` followed later by `-PushOnly`), else stamp a new file from undated `release\Hermes-<Version>-win-x64.exe` using current trigger `yyyy-MM-dd-HHmm`, else fail closed.
  - Forward `--build-stamp $ResolvedBuildStamp` to `synology_release_publisher.py`.
- **Timestamped Artifact Scoping & Validation in `synology_release_publisher.py`**:
  - Add `build_stamp: str | None = None` to `publish_release_artifacts` and `--build-stamp <YYYY-MM-DD-HHmm>` to the CLI, validating `YYYY-MM-DD-HHmm` fail-closed before any credential or network I/O.
  - Scope artifact selection and `latest.yml` validation to `Hermes-<Version>-win-x64-<BuildStamp>.exe` (and `.exe.blockmap`) when `build_stamp` is provided, while preserving undated `Hermes-<Version>-win-x64.exe` compatibility when `build_stamp` is omitted, for both direct Python callers and the CLI (`--build-stamp` is optional, default `None`).

## Capabilities

### Modified Capabilities
- `desktop-release-publishing`: Extend active-version artifact scoping, update-feed validation, and PowerShell build/push modes with trigger-timestamp installer filename stamping (`Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe`).

## Impact

- **Modified Files**:
  - `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1`
  - `O:\workspaces\_config\agent4070\hermes\services\synology_release_publisher.py`
  - `O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py`
- **Zero Upstream Impact**: Zero modifications to `O:\workspaces\oss\hermes-agent\apps\desktop\`.
