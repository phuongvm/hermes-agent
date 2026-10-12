# Change: Upstream Synchronization v11

## Why

Merge `upstream/main` (`ff2d02f998f9f9e45543eea30fb935f47e4504a7`, 207 commits ahead of fork base) into fork `origin/main`, absorbing upstream updates while strictly preserving fork invariants INV-1 through INV-9 and in-tree Honcho memory provider.

## What Changes

- Merge branch `upstream/main` into `sync/upstream-daily` within external isolated worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily`.
- Reconcile upstream changes and preserve all 9 core invariants (INV-1..INV-9), including Windows single-thread OpenBLAS prewarm, Buzz adapter keepalive, Electron Desktop NSIS installer, and coalesced profile reads.
- Reconcile merge conflicts in `pyproject.toml` and `uv.lock`:
  - Adopt upstream 3-day `exclude-newer` policy while maintaining security constraints (`oauthlib>=4.0.0`, `PyJWT==2.15.1`, `urllib3>=2.8.0`, `anyio>=4.14.2`, `tornado==6.5.10`).
  - Preserve in-tree Honcho memory provider optional dependencies (`honcho = ["honcho-ai==2.2.0"]`).
  - Re-generate `uv.lock` cleanly via `uv lock` incorporating all upstream updates and fork pins.
- Run full verification suite:
  - Static typecheck on `@hermes/shared`, `apps/desktop`, and `web` (0 errors).
  - Web UI production bundle build (Vite v8.2.0, 5.52s).
  - Invariant Pytests (`test_entry.py`, `test_buzz_adapter.py`, `test_dashboard_lifecycle_flags.py`, `test_web_read_coalescing.py`, `test_web_server_fs.py`: 229 passed).
  - In-tree Honcho memory suite (`tests/honcho_plugin/`: 465 passed).
  - Desktop NSIS packaging (`Hermes-0.21.6-win-x64.exe`: 121.76 MB).
  - Shadow E2E stack test on port 9129 with online SQLite backup and isolated Honcho workspace.
- Autonomously commit merge (`7ef998089d0`) and push verified branch and release tags to `origin/main`.

## Capabilities

### New Capabilities
- `sync-upstream-main-v11`: Evidence-gated v11 upstream reconciliation contract ensuring invariant preservation, zero live downtime, and full test/packaging verification.

### Modified Capabilities
- Preserved existing fork capabilities across all 9 core invariants (INV-1..INV-9).

## Impact

- Zero live mutation to `O:/workspaces/oss/hermes-agent` or 24/7 background services until Commander cutover.
- Autonomous push to `origin/main` (`7ef998089d0`).
- Full offline cutover script `apply-live-upstream-cutover.ps1` staged for Commander execution.
