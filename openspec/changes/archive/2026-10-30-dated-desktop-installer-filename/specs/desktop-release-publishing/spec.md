## ADDED Requirements

### Requirement: Trigger-Timestamp Installer Filename Stamping
`build-desktop-installer.ps1` SHALL resolve `-BuildStamp` (defaulting on builds to local `YYYY-MM-DD-HHmm` at script start) and stage the NSIS installer as `Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` in `apps/desktop/release/` with zero edits under `apps/desktop/` outside `release/`. Both script and publisher SHALL reject any malformed or invalid calendar/24h timestamp before packaging or network I/O.

#### Scenario: Default trigger timestamp stamps local YYYY-MM-DD-HHmm onto built installer
- **WHEN** `build-desktop-installer.ps1` runs without `-BuildStamp` at local time `2026-10-30 10:55` for version `0.22.4` and `electron-builder` produces `release/win-nsis-build/Hermes-0.22.4-win-x64.exe`
- **THEN** the script SHALL write `release/Hermes-0.22.4-win-x64-2026-10-30-1055.exe` and publish `Hermes-0.22.4-win-x64-2026-10-30-1055.exe` to Synology

#### Scenario: Same-day and cross-day rebuilds of the same version coexist without overwriting
- **WHEN** version `0.22.4` is built and published with `-BuildStamp 2026-10-30-0915` and rebuilt on the same day with `-BuildStamp 2026-10-30-1430`
- **THEN** `Hermes-0.22.4-win-x64-2026-10-30-0915.exe` and `Hermes-0.22.4-win-x64-2026-10-30-1430.exe` SHALL both remain present in `apps/desktop/release/` and in the Synology destination folder
- **AND** the second run SHALL upload only `Hermes-0.22.4-win-x64-2026-10-30-1430.exe`

#### Scenario: Companion blockmap and latest.yml are rewritten to the timestamped filename
- **WHEN** `electron-builder` emits `Hermes-0.22.4-win-x64.exe.blockmap` and `latest.yml` referencing `Hermes-0.22.4-win-x64.exe` and the build script runs with `$BuildStamp = 2026-10-30-1055`
- **THEN** the script SHALL stage `release/Hermes-0.22.4-win-x64-2026-10-30-1055.exe.blockmap` and rewrite `release/latest.yml` so `path` and `files[].url` reference `Hermes-0.22.4-win-x64-2026-10-30-1055.exe`

#### Scenario: Malformed or impossible calendar/time stamp fails closed before build or network I/O
- **WHEN** `-BuildStamp` or `--build-stamp` is given `"2026-02-30-1200"`, `"2026-10-30-2500"`, `"2026-10-30"`, `"202610301055"`, or `"../2026-10-30-1055"`
- **THEN** the tool SHALL exit non-zero naming the invalid build stamp
- **AND** zero `npm` / `electron-builder` invocations and zero DSM network requests SHALL occur

## MODIFIED Requirements

### Requirement: Active Version Artifact Scoping
The workflow SHALL publish only the active installer artifact (`Hermes-<Version>-win-x64-<BuildStamp>.exe` when `build_stamp` / `--build-stamp` is supplied, or `Hermes-<Version>-win-x64.exe` when omitted, in direct Python calls and on the CLI where `--build-stamp` is optional with default `None`), ignoring historical versions, undated leftovers, and other timestamps. With update-feed enabled it SHALL also include `.blockmap` and `latest.yml` validated against the active version and filename before any network request.

#### Scenario: Default run publishes only the current version installer
- **WHEN** `release/` contains `Hermes-0.22.2-win-x64.exe` and `Hermes-0.22.4-win-x64.exe` for a direct Python call with active version `0.22.4` and `build_stamp=None`
- **THEN** only `Hermes-0.22.4-win-x64.exe` SHALL be published to Synology storage
- **AND** `Hermes-0.22.2-win-x64.exe` SHALL NOT be uploaded

#### Scenario: CLI without --build-stamp keeps undated publication working
- **WHEN** the publisher CLI is invoked without `--build-stamp` for version `0.22.4` and `release/` contains `Hermes-0.22.4-win-x64.exe`
- **THEN** it SHALL publish only `Hermes-0.22.4-win-x64.exe` and exit `0`

#### Scenario: Default timestamped run publishes only the active version and active stamp installer
- **WHEN** `release/` contains `Hermes-0.22.2-win-x64-2026-10-30-1055.exe`, `Hermes-0.22.4-win-x64.exe`, `Hermes-0.22.4-win-x64-2026-10-30-0915.exe`, and `Hermes-0.22.4-win-x64-2026-10-30-1055.exe` for active version `0.22.4` and `build_stamp="2026-10-30-1055"`
- **THEN** only `Hermes-0.22.4-win-x64-2026-10-30-1055.exe` SHALL be published to Synology storage
- **AND** none of the other files SHALL be uploaded

#### Scenario: Companion update-feed option publishes trio
- **WHEN** the operator enables the update-feed companion flag for version `0.22.4` (producing `Hermes-0.22.4-win-x64-2026-10-30-1055.exe` when `build_stamp="2026-10-30-1055"`, or `Hermes-0.22.4-win-x64.exe` when `build_stamp=None`)
- **THEN** the active `.exe`, its matching `.exe.blockmap`, and `latest.yml` SHALL all be uploaded and verified

#### Scenario: Stale update feed is rejected before any upload
- **WHEN** the operator enables the update-feed companion flag for version `0.22.4` and `latest.yml` declares `version: 0.22.2` or references an installer/blockmap other than the active expected filename (including undated `Hermes-0.22.4-win-x64.exe` or earlier stamp `2026-10-30-0915` when `build_stamp="2026-10-30-1055"`)
- **THEN** the publisher SHALL exit non-zero naming the mismatch
- **AND** zero DSM network requests (probe, login, upload) SHALL be made

#### Scenario: Foreign artifact name is rejected
- **WHEN** the publisher is asked to publish `Hermes-0.22.2-win-x64.exe` or `Hermes-0.22.4-win-x64-2026-10-30-0915.exe` for active version `0.22.4` and `build_stamp="2026-10-30-1055"`
- **THEN** it SHALL exit non-zero before any network request

### Requirement: Build Script Execution Modes and Fail-Closed Propagation
The build script SHALL stage `Hermes-<Version>-win-x64-<BuildStamp>.exe` and publish to Synology after a successful build by default, propagating any publication failure as a non-zero exit code. It SHALL support `-BuildStamp` (default local `yyyy-MM-dd-HHmm` on builds), `-SkipPush`, `-PushOnly` (mutually exclusive), `-IncludeUpdateFeed`, `-SynologyDestPath`, `-SynologyDsmUrl`, `-HermesConfigPath`, and `-BwBinary`.

#### Scenario: Local-only build skips NAS network calls
- **WHEN** the build script is invoked with `-SkipPush` (with or without `-BuildStamp`)
- **THEN** packaging completes locally producing `Hermes-<Version>-win-x64-<BuildStamp>.exe` in `apps/desktop/release/` and zero Synology network requests are made

#### Scenario: Push-only mode publishes existing artifact without invoking Electron packaging
- **WHEN** one or more valid stamped `Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` files exist in `apps/desktop/release/` and `-PushOnly` is invoked (selecting the exact `-BuildStamp` when given, or, when `-BuildStamp` is omitted, the latest lexicographic `<YYYY-MM-DD-HHmm>` stamp for `<Version>` taken only from candidates whose captured stamp passes calendar and 24-hour validation)
- **THEN** the script SHALL skip `npm run build` and `electron-builder` and immediately publish and verify that stamped installer

#### Scenario: Push-only mode stamps timestamped artifact from undated active-version installer when no stamped copy exists
- **WHEN** the target stamped installer does not exist in `apps/desktop/release/`, undated `Hermes-<Version>-win-x64.exe` exists in `apps/desktop/release/`, and `-PushOnly` is invoked (with `-BuildStamp <S>` or with no stamped `<Version>` installer present)
- **THEN** the script SHALL copy `Hermes-<Version>-win-x64.exe` to `Hermes-<Version>-win-x64-<ResolvedBuildStamp>.exe` (and stage the stamped `.blockmap` and rewritten active-version `latest.yml` whenever the undated companion files exist, regardless of `-IncludeUpdateFeed`) and publish `Hermes-<Version>-win-x64-<ResolvedBuildStamp>.exe`

#### Scenario: Push-only ignores impossible on-disk stamps when selecting the latest
- **WHEN** `apps/desktop/release/` contains `Hermes-0.22.4-win-x64-9999-99-99-9999.exe` and `Hermes-0.22.4-win-x64-2026-10-30-0915.exe` and `-PushOnly` is invoked without `-BuildStamp`
- **THEN** the script SHALL publish `Hermes-0.22.4-win-x64-2026-10-30-0915.exe` and SHALL NOT select the impossible stamp

#### Scenario: Stamped companions are staged independently of -IncludeUpdateFeed
- **WHEN** undated `Hermes-0.22.4-win-x64.exe`, `.exe.blockmap` and `latest.yml` exist, `-PushOnly` is run first without `-IncludeUpdateFeed`, and then `-PushOnly -IncludeUpdateFeed` is run without `-BuildStamp`
- **THEN** the first run SHALL stage the stamped `.exe`, stamped `.exe.blockmap` and rewritten `latest.yml`
- **AND** the second run SHALL select that stamped installer and publish the stamped `.exe`, `.exe.blockmap` and `latest.yml` without manual recovery

#### Scenario: Default build publishes only the active version, never stale staged installers
- **WHEN** `release/win-nsis-build/` holds `Hermes-0.22.2-win-x64.exe` and freshly built `Hermes-0.22.4-win-x64.exe` and a default (push-enabled) build succeeds at `2026-10-30-1055`
- **THEN** only `Hermes-0.22.4-win-x64-2026-10-30-1055.exe` SHALL be staged to the release directory and published

#### Scenario: Publication failure propagates
- **WHEN** the publisher exits non-zero
- **THEN** the build script SHALL exit non-zero

#### Scenario: Push-only mode fails closed when artifact is missing
- **WHEN** `-PushOnly` is invoked and no matching stamped `Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` and no undated `Hermes-<Version>-win-x64.exe` exist locally in `apps/desktop/release/` (or explicit `-BuildStamp` matches neither a stamped file nor an undated fallback)
- **THEN** the script SHALL exit non-zero with a clear message instructing the operator to run a build first
