## Context
Promotes two APPROVED 2026-10-30 explorations of `build-desktop-installer.ps1` into the canonical `desktop-release-publishing` spec. Behavior is already implemented in `_config/agent4070/hermes/services/build-desktop-installer.ps1` (`Initialize-HermesToolchain`, `Assert-DesktopDependencies`, `Resolve-PublisherPython`, `Test-WorkingPython`) and covered at Seam B by `TestSeamBToolchainAndDependencyRepair` and `TestSeamBPublisherPython`; the full `test_synology_release_publisher.py` suite is 190 tests.

## Goals / Non-Goals
- Goals: record observable contracts at the public `pwsh.exe -File build-desktop-installer.ps1` seam; keep the canonical spec at 9 requirements.
- Non-Goals: any code change; edits to `apps/desktop/`, `hermes-env.cmd` or `synology_release_publisher.py`; archiving this change (Commander chose to pause before archive).

## Decisions
1. **Two requirements, not five.** Toolchain ladder and dependency repair share one build-preparation contract; Python resolution is a separate publish-step contract. Canonical total: 7 + 2 = 9.
2. **Observable behavior only.** Scenarios state process outcomes (Node used, npm calls, exit code, DSM request count) rather than private helper internals, matching the no-change-detector-test standard. Helper names appear only in requirement prose as the public ladder seam.
3. **Toolchain ladder order.** Override, then PM `source_build_env`, then `<HermesHome>\tools` semantic-version fallback. Version ordering is numeric so `26.12.1 > 26.7.0`. Unconditional on non-`-PushOnly` runs and not gated on `hermes-env.cmd` (a Round-1 review defect, closed in Round 2).
4. **Repair with `node-deps.mjs`, never `npm install`.** `npm install` can mutate `package-lock.json`, prune shared workspaces and skip Electron runtime staging; `node-deps.mjs --reuse` keeps the union of workspaces intact. Locked `npm ci` is the fallback only when the script is absent.
5. **Live-probe Python gate.** Path-pattern rejection of `Microsoft\WindowsApps` alone is insufficient (a broken non-Store shim is also possible), so every candidate runs a Python >=3.10 probe.
6. **Fail closed first.** Toolchain, repair and Python failures all exit non-zero before packaging or any DSM request.

## Risks / Trade-offs
- The spec mirrors a PowerShell script outside the repo (`_config`); drift is guarded by the Seam B suite, which is the proof command.
- Windows-only behavior (`WindowsApps`, `.cmd` shims) is specified; other platforms are out of scope for this script.

## Migration Plan
Sync the delta into `openspec/specs/desktop-release-publishing/spec.md` (done in this change). Archive is deferred until the Commander approves.
