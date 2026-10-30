# Proposal: Hermes Desktop Installer Auto-Push to Synology Releases

## Why

Building the Hermes Desktop Windows NSIS installer via `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` currently stops at local artifact staging (`apps/desktop/release/`), requiring the operator to manually authenticate to Synology DSM (`dsm16`), navigate FileStation, and upload a ~124 MB executable by hand. Automating post-build publication directly into `/volume1/_Archives/inspire-robotics/project_files/Releases` eliminates manual toil, prevents uploading stale cached builds from `win-nsis-build/`, routes around Cloudflare Tunnel's 100 MB body cap via WireGuard DSM WebAPI, and enforces zero-trust byte-size verification on every release.

## What Changes

- Introduce a standalone, zero-dependency Python stdlib release publisher module (`O:\workspaces\_config\agent4070\hermes\services\synology_release_publisher.py`) exposing a narrow callable and CLI seam (`publish_release_artifacts`).
- Implement an observable DSM Endpoint Ladder that prioritizes direct WireGuard DSM WebAPI endpoints (`https://10.8.0.60:5001` → `http://10.8.0.60:5000`, bypassing Cloudflare's 100 MB upload limit for ~124 MB installers) before falling back to `https://dsm16.ptdev.vip`, with explicit single-URL override via `--dsm-url` / process environment `SYNOLOGY_DSM_URL` and an injectable ordered `candidates` list as the public test seam for the ladder.
- Implement automatic Synology path normalization converting Linux volume paths (`/volume1/_Archives/inspire-robotics/project_files/Releases`) into FileStation shared-folder paths (`/_Archives/inspire-robotics/project_files/Releases`).
- Implement unattended DSM authentication (`SYNO.API.Auth` `session=FileStation` with `SynoToken` support) reading `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` from environment variables or `O:\workspaces\_config\agent4070\hermes\.env`, with guaranteed session logout in a `finally` block and zero secret echoing.
- Implement streaming 8 MB-chunked `multipart/form-data` upload (`SYNO.FileStation.Upload` v2/v3) with strict metadata-before-file field ordering (`overwrite=true`, `create_parents=true`) followed by zero-trust remote byte-size verification via `SYNO.FileStation.List`.
- Enhance `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` to scope artifact syncing and uploading strictly to the active `$InstallerVersion` (`Hermes-$InstallerVersion-win-x64.exe`), trigger the fail-closed Synology push automatically after build success, and support `-SkipPush`, `-PushOnly`, `-IncludeUpdateFeed`, `-SynologyDestPath` (and `-SynologyDsmUrl`) parameters. `latest.yml` is validated against the active version before any upload.
- Add end-to-end automated tests (`O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py`) exercising both public seams against a real loopback Fake DSM HTTP server.

## Capabilities

### New Capabilities
- `desktop-release-publishing`: Automated, fail-closed post-build publication and remote integrity verification of Hermes Desktop installer artifacts to Synology DSM FileStation storage.

### Modified Capabilities
None.

## Impact

- **Service Scripts (`O:\workspaces\_config\agent4070\hermes\services\`)**:
  - `build-desktop-installer.ps1`: Extended with `-SkipPush`, `-PushOnly`, `-IncludeUpdateFeed`, `-SynologyDestPath`, version-scoped artifact selection, and post-build publisher invocation.
  - `synology_release_publisher.py`: New deep publisher module using Python 3.14 standard library only.
  - `tests/test_synology_release_publisher.py`: Loopback DSM integration & PowerShell CLI seam test suite.
- **Configuration Secrets (`O:\workspaces\_config\agent4070\hermes\.env`)**:
  - Consumes secrets `SYNOLOGY_DSM_USER`, `SYNOLOGY_DSM_PASS` only. Non-secret endpoint selection (`-SynologyDsmUrl` / process env `SYNOLOGY_DSM_URL`) is not stored in `.env`.
- **In-Tree Repository Impact (`O:\workspaces\oss\hermes-agent`)**:
  - Zero edits to `apps/desktop/` or core Python runtime files; preserves `installer-version-integrity` and `upstream-sync-preservation` invariants with zero upstream merge conflict surface.
