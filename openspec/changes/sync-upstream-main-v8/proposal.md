# Change: Upstream Synchronization v8

## Why

Merge `upstream/main` (`865ba906c1a8d93de65839ee7af487204d42e873`, 490 commits ahead of fork base) into fork `origin/main` (`dee296f1c1a2`), absorbing upstream updates while strictly preserving fork invariants INV-1 through INV-9 and in-tree Honcho memory provider.

## What Changes

- Merge branch `upstream/main` into `sync/upstream-daily` within external isolated worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily`.
- Reconcile unmerged files:
  - `web/src/components/ChatSessionList.test.tsx`: Preserved TypeScript strict types (`type ReactNode`, `globalThis as typeof globalThis & ...`, and `prefix?: ReactNode` button props) to ensure web workspace typecheck passes cleanly.
- Resolved web compiler artifact collision in `scripts/daily-sync-watchdog.ps1` to ensure pre-existing junctions at `hermes_cli/web_dist` are cleared before building.
- Preserved all 9 core invariants (INV-1..INV-9), including Windows OpenBLAS prewarm, Buzz adapter keepalive, Electron Desktop NSIS installer, and coalesced profile reads.

## Capabilities

### New Capabilities
- `sync-upstream-main-v8`: Evidence-gated v8 upstream reconciliation contract ensuring invariant preservation, zero live downtime, and full test/packaging verification.

### Modified Capabilities
- Preserved existing fork capabilities across all 9 core invariants (INV-1..INV-9).

## Impact

- 1 conflict file hand-reconciled in isolated worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily`.
- Zero live mutation until 100% attested green on Typecheck, Unit Tests, NSIS Desktop packaging, and Shadow E2E (port 9129).
- Autonomous push to `origin/main` (`b5d97dd8b02`) and upstream release tags propagated.
