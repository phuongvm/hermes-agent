# Change: Upstream Synchronization v7

## Why

Merge `upstream/main` (`343500b3547e`, 823 commits ahead of fork base) into fork `origin/main` (`beca55ec990`), absorbing upstream updates while strictly preserving fork invariants INV-1 through INV-9 and in-tree Honcho memory provider.

## What Changes

- Merge branch `upstream/main` into `sync/upstream-daily` within external isolated worktree `O:/workspaces/wt-hermes-sync-daily`.
- Reconcile 2 unmerged files:
  - `hermes_cli/web_server_files.py`: Reconciled path candidate resolution (`_resolve_fs_candidate`), percent-decode fallback for remote client hops, MSYS-to-Windows path normalization, and fork's hidden directory 403 protection (`_is_hidden_path`) and Windows root normalization (`_fs_default_cwd`).
  - `tests/hermes_cli/test_web_server_fs.py`: Preserved both fork's hidden directory/config security tests and upstream's double-encoded non-ASCII and literal percent path tests.
- Preserved all 9 core invariants (INV-1..INV-9), including Windows OpenBLAS prewarm, Buzz adapter keepalive, Electron Desktop NSIS installer, and coalesced profile reads.

## Capabilities

### New Capabilities
- `sync-upstream-main-v7`: Evidence-gated v7 upstream reconciliation contract ensuring invariant preservation, zero live downtime, and full test/packaging verification.

### Modified Capabilities
- Preserved existing fork capabilities across all 9 core invariants (INV-1..INV-9).

## Impact

- 2 files hand-reconciled in isolated worktree `O:/workspaces/wt-hermes-sync-daily`.
- Zero live mutation until 100% attested green on Typecheck, Unit Tests, NSIS Desktop packaging, and Shadow E2E (port 9129).
