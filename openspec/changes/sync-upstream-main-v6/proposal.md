# Change: Upstream Synchronization v6

## Why

Merge `upstream/main` (`bed0d535556b`, 1,249 commits ahead of fork base) into fork `origin/main` (`2f49506a780`), absorbing upstream updates while strictly preserving fork invariants INV-1 through INV-9. Upstream has removed bundled `plugins/memory/honcho` in favor of a catalog plugin (`7e53b3ef82c`); the fork must preserve its custom, hardened in-tree Honcho implementation (own-peer reconciliation, bot author propagation, M2 fixes) to prevent regression across all cluster profiles.

## What Changes

- Merge branch `upstream/main` into `sync/upstream-daily` within external isolated worktree `O:/workspaces/wt-hermes-sync-daily`.
- Reconcile 21 conflicted files:
  - **Memory (Honcho)**: Preserve in-tree `plugins/memory/honcho/` (`README.md`, `__init__.py`, `client.py`, `session.py`). Retain fork's robust multi-profile memory provider and update doctor checks accordingly.
  - **Desktop Build & Packaging (INV-8)**: Preserve NSIS packaging admission in `apps/desktop/electron-builder.config.cjs` and ensure `stage-gateway-connection.mjs` runs during `apps/desktop/scripts/build.mjs`.
  - **Desktop Core & Review Ops (INV-3)**: Reconcile `apps/desktop/electron/main.ts`, `apps/desktop/electron/preload.ts`, and `apps/desktop/electron/git-review-ops.ts`, keeping `reauthModalLatch`, version ladder fallbacks, and review operations.
  - **Desktop UI & Store**: Merge state updates in `apps/desktop/src/app/contrib/panes.tsx`, `apps/desktop/src/lib/local-preview.ts`, `apps/desktop/src/store/review.ts`, and their test suites.
  - **CLI & Web Routers (INV-5, INV-6)**: Reconcile `hermes_cli/dashboard_auth/routes.py`, `hermes_cli/doctor_tools.py`, `hermes_cli/web_routers/files.py`, `hermes_cli/web_server_chat.py`, and `web/src/pages/ModelsPage.tsx`.

## Capabilities

### New Capabilities
- `sync-upstream-main-v6`: Evidence-gated v6 upstream reconciliation contract ensuring invariant preservation, zero live downtime, and full test/packaging verification.

### Modified Capabilities
- Preserved existing fork capabilities across all 9 core invariants (INV-1..INV-9).

## Impact

- 21 files hand-reconciled in isolated worktree `O:/workspaces/wt-hermes-sync-daily`.
- Zero live mutation until 100% attested green on Typecheck, Unit Tests, NSIS Desktop packaging, and Shadow E2E (port 9129).
