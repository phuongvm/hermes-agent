# Change: Promote desktop installer PM toolchain, dependency repair and publisher Python guard into the spec

## Why
Two 2026-10-30 `/crew-no-spec` fixes to `build-desktop-installer.ps1` were implemented and independently reviewed (both APPROVED) but are not yet in the canonical `desktop-release-publishing` spec:

1. The build used a hardcoded stale Node 22 and ignored `npm` install failures, so `assert-root-install.mjs` failed after a Hermes restart (`EBADENGINE`, engines require node >=26).
2. The Synology publication step resolved `python` from ambient `PATH`, hitting the Windows 11 Microsoft Store `WindowsApps\python.exe` alias stub (exit 9009).

Without spec coverage, later edits could regress either behavior silently.

## What Changes
- ADDED requirement **Hermes PM Toolchain Ladder for Desktop Builds** (node >=26 / npm >=12 via `-ToolchainBinDir`, `source_build_env`, then `<HermesHome>\tools` newest semantic version; never a hardcoded Node).
- ADDED requirement **Fail-Closed Desktop Workspace Dependency Repair** (`assert-root-install.mjs` guard, `node-deps.mjs` union repair or locked `npm ci`, never `npm install`).
- ADDED requirement **Publisher Python Resolution Rejects Store Alias Stubs** (`Resolve-PublisherPython` + `Test-WorkingPython` live-probe ladder, fail closed before any network I/O).
- No code change: the implementation and its 190 Seam A + Seam B tests already exist and pass.

## Capabilities

### Modified Capabilities
- `desktop-release-publishing`: three requirements added (7 -> 10 would be incorrect; see design.md Decision 1 for the two-requirement grouping that yields 9 total).

## Impact
- Spec only: `openspec/specs/desktop-release-publishing/spec.md` (synced after validation).
- Reference implementation (unchanged): `_config/agent4070/hermes/services/build-desktop-installer.ps1`; tests `_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`.
- Zero diff to `apps/desktop/`, `synology_release_publisher.py` and `hermes-env.cmd`.
- Sources: `openspec/workspace/explorations/2026-10-30-fix-desktop-installer-build-failure-exploration.md`, `.../2026-10-30-fix-publisher-python-windowsapps-alias-exploration.md`; reviews `.../2026-10-30-fix-desktop-installer-build-failure-review-v2.md`, `.../2026-10-30-fix-publisher-python-windowsapps-alias-review.md`.
