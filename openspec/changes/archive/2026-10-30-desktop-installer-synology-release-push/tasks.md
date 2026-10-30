# Tasks: Hermes Desktop Installer Auto-Push to Synology Releases

## 1. Tracer Bullet 1: Deep Synology Release Publisher Module & Seam A Loopback DSM Tests

- [x] 1.1 [Blocked by: None] Author Seam A loopback Fake DSM HTTP server TDD suite (`O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`) and implement `O:/workspaces/_config/agent4070/hermes/services/synology_release_publisher.py` covering `/volume<N>` path normalization, `.env` credential loading without secret echoing, observable Endpoint Ladder (`https://10.8.0.60:5001` -> `http://10.8.0.60:5000` -> `https://dsm16.ptdev.vip` plus `SYNOLOGY_DSM_URL` override), `SYNO.API.Auth` login with guaranteed `finally` logout, 8 MB-chunked `SYNO.FileStation.Upload` with metadata-before-file ordering (`overwrite=true`, `create_parents=true`), and `SYNO.FileStation.List` exact byte-size verification. Proof: `python -m pytest "O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py" -k "SeamA" -q` exits 0.

## 2. Tracer Bullet 2: Build Script Post-Build Integration & Seam B Process Tests

- [x] 2.1 [Blocked by: 1.1] Author Seam B PowerShell process integration tests in `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py` and enhance `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1` with active `$InstallerVersion` artifact scoping (preventing stale `win-nsis-build` `.exe` uploads), fail-closed post-build invocation of `synology_release_publisher.py`, and `-SkipPush`, `-PushOnly`, `-IncludeUpdateFeed`, and `-SynologyDestPath` parameters. Proof: `python -m pytest "O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py" -q` exits 0.

## 3. Tracer Bullet 3: Independent Verification, Repo Cleanliness & Strict Spec Gate

- [x] 3.1 [Blocked by: 2.1] Verify zero modifications to `O:/workspaces/oss/hermes-agent/apps/desktop/`, execute the complete Seam A + Seam B test suite, and pass strict OpenSpec validation (plus live NAS `-PushOnly` smoke verification once `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` are configured in `O:/workspaces/_config/agent4070/hermes/.env`). Proof: `"C:/nvm4w/nodejs/openspec.exe" validate desktop-installer-synology-release-push --strict && python -m pytest "O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py" -q` exits 0.

## Workflow follow-up

- Sync `specs/desktop-release-publishing/spec.md` into `openspec/specs/desktop-release-publishing/spec.md` via `openspec-sync-specs` after implementation and verification pass.
- Archive `desktop-installer-synology-release-push` via `openspec archive desktop-installer-synology-release-push --skip-specs -y` only after explicit Commander approval.
