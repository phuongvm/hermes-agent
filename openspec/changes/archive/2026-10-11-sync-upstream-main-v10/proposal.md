# Change: Upstream Synchronization v10

## Why

Merge `upstream/main` (`4eba3ebed1513d1efe45802f396a11679fff02cf`, 233 commits ahead of fork base `61cc97d61c9e`) into fork `origin/main`, absorbing upstream updates while strictly preserving fork invariants INV-1 through INV-9 and in-tree Honcho memory provider.

## What Changes

- Merge branch `upstream/main` into `sync/upstream-daily` within external isolated worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily`.
- Reconcile upstream changes and preserve all 9 core invariants (INV-1..INV-9), including Windows single-thread OpenBLAS prewarm, Buzz adapter keepalive, Electron Desktop NSIS installer, and coalesced profile reads.
- Reconcile single merge conflict in `tests/scripts/install/test_install_ps1_native_calls.py` (adopt upstream unused tuple unpacking `_, frame = ...`).
- Auto-preserve in-tree Honcho memory provider (`plugins/memory/honcho/`, `tests/honcho_plugin/`).
- Run full verification suite:
  - Static typecheck on `@hermes/shared`, `apps/desktop`, and `web` (0 errors).
  - Web UI production bundle build (Vite v8.2.0, 5.19s).
  - Invariant Pytests (`test_entry.py`, `test_buzz_adapter.py`, `test_dashboard_lifecycle_flags.py`, `test_web_read_coalescing.py`, `test_web_server_fs.py`: 229 passed).
  - In-tree Honcho memory suite (`tests/honcho_plugin/`: 465 passed).
  - Desktop NSIS packaging (`Hermes-0.21.6-win-x64.exe`: 121.76 MB).
  - Shadow E2E stack test on port 9129 with online SQLite backup and isolated Honcho workspace.
- Autonomously push verified merge commit `6f2a4a488e8` to `origin/main`.

## Capabilities

### New Capabilities
- `sync-upstream-main-v10`: Evidence-gated v10 upstream reconciliation contract ensuring invariant preservation, zero live downtime, and full test/packaging verification.

### Modified Capabilities
- Preserved existing fork capabilities across all 9 core invariants (INV-1..INV-9).

## Impact

- Zero live mutation to `O:/workspaces/oss/hermes-agent` or 24/7 background services until Commander cutover.
- Autonomous push to `origin/main` (`6f2a4a488e8`).
- Full offline cutover script `apply-live-upstream-cutover.ps1` staged for Commander execution.
