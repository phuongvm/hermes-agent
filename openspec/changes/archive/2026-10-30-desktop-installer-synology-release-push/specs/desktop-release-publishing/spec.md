# Specification: Desktop Release Publishing

## Purpose

Governs automated, fail-closed post-build publication and remote integrity verification of Hermes Desktop Windows installer artifacts to Synology DSM FileStation storage.

## ADDED Requirements

### Requirement: Active Version Artifact Scoping
The workflow SHALL publish only `Hermes-<Version>-win-x64.exe` for the resolved build version, ignoring historical staged artifacts. With update-feed publication enabled it SHALL also include the `.blockmap` and `latest.yml`, and `latest.yml` SHALL be validated against the active version before any network request.

#### Scenario: Default run publishes only the current version installer
- **WHEN** the staging directory contains both `Hermes-0.22.2-win-x64.exe` and `Hermes-0.22.4-win-x64.exe` for an active build of version `0.22.4`
- **THEN** only `Hermes-0.22.4-win-x64.exe` SHALL be published to Synology storage
- **AND** `Hermes-0.22.2-win-x64.exe` SHALL NOT be uploaded

#### Scenario: Companion update-feed option publishes trio
- **WHEN** the operator enables the update-feed companion flag for version `0.22.4`
- **THEN** `Hermes-0.22.4-win-x64.exe`, `Hermes-0.22.4-win-x64.exe.blockmap`, and `latest.yml` SHALL all be uploaded and verified

#### Scenario: Stale update feed is rejected before any upload
- **WHEN** the operator enables the update-feed companion flag for version `0.22.4` and `latest.yml` declares `version: 0.22.2` or references an installer/blockmap other than `Hermes-0.22.4-win-x64.exe[.blockmap]`
- **THEN** the publisher SHALL exit non-zero naming the mismatch
- **AND** zero DSM network requests (probe, login, upload) SHALL be made

#### Scenario: Foreign artifact name is rejected
- **WHEN** the publisher is asked to publish `Hermes-0.22.2-win-x64.exe` for active version `0.22.4`
- **THEN** it SHALL exit non-zero before any network request

### Requirement: Observable DSM Endpoint Ladder
When no explicit DSM base URL override is configured, the publisher SHALL probe candidate endpoints in strict priority order: direct WireGuard HTTPS (`https://10.8.0.60:5001`), direct WireGuard HTTP (`http://10.8.0.60:5000`), and public Cloudflare Tunnel (`https://dsm16.ptdev.vip`), selecting the first endpoint whose `SYNO.API.Info` probe succeeds.

#### Scenario: Direct WireGuard endpoint preferred over Cloudflare Tunnel
- **WHEN** `SYNOLOGY_DSM_URL` is unset and `https://10.8.0.60:5001` responds to `SYNO.API.Info`
- **THEN** the publisher SHALL select `https://10.8.0.60:5001` so payloads exceeding 100 MB bypass Cloudflare body limits

#### Scenario: Unreachable primary rung falls back to next reachable rung
- **WHEN** `https://10.8.0.60:5001` times out and `http://10.8.0.60:5000` responds to `SYNO.API.Info`
- **THEN** the publisher SHALL fall back to `http://10.8.0.60:5000` and complete publication

#### Scenario: Explicit URL override disables default ladder
- **WHEN** a DSM base URL override is supplied (`--dsm-url` / `dsm_url=` argument, else process environment variable `SYNOLOGY_DSM_URL`; the argument takes precedence; the override is never read from `.env`)
- **THEN** the publisher SHALL probe only that configured URL and fail closed if it is unreachable

#### Scenario: Ladder order is injectable through one public test seam
- **WHEN** `publish_release_artifacts(..., candidates=[<url1>, <url2>, ...])` (CLI: `--candidates url1,url2`) is supplied without a URL override
- **THEN** the publisher SHALL probe exactly those candidates in the given order and select the first successful `SYNO.API.Info` probe
- **AND** when `candidates` is omitted the candidate list SHALL equal the three production defaults in the strict order above (observable through `endpoint_ladder()`)

### Requirement: Synology Volume Path Normalization
The publisher SHALL accept both Linux volume-prefixed paths (`/volume<N>/<share>/...`) and FileStation shared-folder paths (`/<share>/...`) and normalize them to the shared-folder root path required by `SYNO.FileStation.Upload` and `SYNO.FileStation.List`.

#### Scenario: Volume-prefixed target path is normalized
- **WHEN** the destination path is `/volume1/_Archives/inspire-robotics/project_files/Releases`
- **THEN** FileStation API requests SHALL target `/_Archives/inspire-robotics/project_files/Releases`

#### Scenario: Shared-folder path is preserved
- **WHEN** the destination path is `/_Archives/inspire-robotics/project_files/Releases/`
- **THEN** FileStation API requests SHALL target `/_Archives/inspire-robotics/project_files/Releases` without trailing-slash duplication

### Requirement: Unattended Session Lifecycle and Secret Hygiene
The publisher SHALL load `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` from the process environment, falling back to the Hermes `.env`, never echo them, log in via `SYNO.API.Auth` (`session=FileStation`, `enable_syno_token=yes`), and always `method=logout` afterwards. Every FileStation request SHALL carry `_sid`, the `id` cookie and `X-SYNO-TOKEN`; a login without `synotoken` SHALL fail closed.

#### Scenario: Missing credentials fail closed with actionable message
- **WHEN** `SYNOLOGY_DSM_USER` or `SYNOLOGY_DSM_PASS` is absent from both process environment and `.env`
- **THEN** the publisher SHALL exit non-zero before attempting authentication and name the missing variable(s) without printing any secret content

#### Scenario: Session logout executes even after upload failure
- **WHEN** `SYNO.API.Auth` login succeeds and a subsequent `SYNO.FileStation.Upload` request fails
- **THEN** the publisher SHALL still invoke `SYNO.API.Auth` `method=logout` for that session before exiting non-zero

#### Scenario: Missing SynoToken fails closed
- **WHEN** login succeeds but the response contains no `synotoken`
- **THEN** the publisher SHALL make no FileStation request, SHALL invoke `method=logout`, and SHALL exit non-zero

#### Scenario: Token-less or mismatched-token FileStation requests are rejected
- **WHEN** the DSM rejects a FileStation request lacking or mismatching `X-SYNO-TOKEN` / `_sid`
- **THEN** the publisher SHALL surface the DSM error code, log out, and exit non-zero

### Requirement: Streaming Multipart Upload and Remote Byte-Size Verification
The publisher SHALL stream each artifact via `SYNO.FileStation.Upload` with `overwrite=true` and `create_parents=true`, placing all metadata fields before the binary file payload, and SHALL verify via `SYNO.FileStation.List` that the remote file exists and its byte size equals the local file byte size.

#### Scenario: Upload succeeds and remote byte size matches local artifact
- **WHEN** a 124 MB installer executable is uploaded to `/_Archives/inspire-robotics/project_files/Releases`
- **THEN** all multipart metadata fields precede the binary payload
- **AND** `SYNO.FileStation.List` confirms the remote file byte size equals the local file byte size
- **AND** the publisher exits with code 0

#### Scenario: Remote size mismatch fails closed
- **WHEN** `SYNO.FileStation.Upload` returns success but `SYNO.FileStation.List` reports a byte size differing from the local file
- **THEN** the publisher SHALL report the byte-size discrepancy and exit non-zero

### Requirement: Build Script Execution Modes and Fail-Closed Propagation
The build script SHALL publish to Synology after a successful build by default and propagate any publication failure as a non-zero exit code. It SHALL support `-SkipPush`, `-PushOnly` (mutually exclusive), `-IncludeUpdateFeed`, `-SynologyDestPath` (default `/volume1/_Archives/inspire-robotics/project_files/Releases`) and `-SynologyDsmUrl` (overrides env `SYNOLOGY_DSM_URL`).

#### Scenario: Local-only build skips NAS network calls
- **WHEN** the build script is invoked with `-SkipPush`
- **THEN** packaging completes locally and zero Synology network requests are made

#### Scenario: Push-only mode publishes existing artifact without invoking Electron packaging
- **WHEN** `Hermes-<Version>-win-x64.exe` already exists in the release directory and the script is invoked with `-PushOnly`
- **THEN** the script SHALL skip `npm run build` and `electron-builder` and immediately publish and verify `Hermes-<Version>-win-x64.exe`

#### Scenario: Default build publishes only the active version, never stale staged installers
- **WHEN** the staging directory holds `Hermes-0.22.2-win-x64.exe` and the freshly built `Hermes-0.22.4-win-x64.exe` and a default (push-enabled) build succeeds
- **THEN** only `Hermes-0.22.4-win-x64.exe` SHALL be copied to the release directory and published

#### Scenario: Publication failure propagates
- **WHEN** the publisher exits non-zero
- **THEN** the build script SHALL exit non-zero

#### Scenario: Push-only mode fails closed when artifact is missing
- **WHEN** `-PushOnly` is invoked and `Hermes-<Version>-win-x64.exe` does not exist locally
- **THEN** the script SHALL exit non-zero with a clear message instructing the operator to run a build first
