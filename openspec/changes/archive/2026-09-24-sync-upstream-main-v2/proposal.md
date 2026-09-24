# Proposal: Evidence-Gated Upstream Main Reconciliation (v2)

## Why

The local fork `origin/main` (at `43ca20a5fd`) is 180 commits ahead of the common merge-base (`24fd22b94d`), while upstream `upstream/main` (at `c661785f87`) has advanced by 1,360 commits. Integrating this substantial upstream progression without an explicit behavioral preservation contract risks regressing critical local stability and security fixes, specifically: host-bound OIDC authentication, Desktop 401 cascade suppression, session reconnect resilience, Buzz WebSocket keepalive tuning, and multi-provider JWKS kid classification. Empirical verification and structured conflict triage are mandatory to ensure no local invariant is compromised.

## What Changes

- Reconcile `upstream/main` (`c661785f87`) into an isolated integration worktree (`.worktrees/sync-upstream-main-v2`), preserving `origin/main` untouched until formal Commander approval.
- Catalog all 10 content conflict files identified via 3-way merge-tree analysis (`acp_adapter/session.py`, `apps/desktop/electron/main.ts`, `apps/desktop/scripts/set-exe-identity.mjs`, `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts`, `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts`, `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx`, `plugins/platforms/buzz/adapter.py`, `tests/hermes_cli/test_mcp_startup.py`, `tui_gateway/methods_prompt.py`, and `uv.lock`).
- Define explicit preservation strategies for non-negotiable invariants SYNC-3 through SYNC-7:
  - **SYNC-3**: Process-home authentication ownership, session token persistence, and startup grace in dashboard auth.
  - **SYNC-4**: Bounded native bearer 401 recovery, single-replay execution, and generation tracking in Desktop.
  - **SYNC-5**: Terminal-auth suspension distinct from transient network reconnection in Desktop renderer and project tree.
  - **SYNC-6**: Buzz WebSocket idle keepalive tuning (30s interval / 60s timeout) and non-editable delivery (`SUPPORTS_MESSAGE_EDITING = False`).
  - **SYNC-7**: Self-hosted OIDC verifier JWKS foreign-kid classification and canonical native chooser redirect integrity.
- Establish an empirical resolution ledger (`resolution-ledger.md`) requiring line-by-line justification, before/after evidence, and independent verification for every conflict resolution and auto-merged invariant dependency.
- Reconcile toolchain and package locks (`uv.lock`, `package-lock.json`) in hermetic environments to enforce invariant SYNC-8.

## Capabilities

### New Capabilities

- `upstream-sync-preservation`: An evidence-gated integration contract covering isolated reconciliation, conflict matrix cataloging, preservation of core authentication and resilience invariants (SYNC-3 through SYNC-7), reproducible dependencies, and independent multi-agent acceptance.

### Modified Capabilities

None. This change preserves and protects existing requirements in `dashboard-auth`, `desktop-reconnect-resilience`, `buzz-websocket`, `remote-fs-security`, and `desktop-remote-file-preview` without weakening their guarantees.

## Impact

- **Local Baseline**: `43ca20a5fd25ec415315bc93ca89370d4fd872b9` (`origin/main`).
- **Upstream Target**: `c661785f872b5647fbac7c138d965180783bd9af` (`upstream/main`, 1,360 commits ahead).
- **Common Merge-Base**: `24fd22b94df040d843eb280ff197a4bcd99a6fc3` (180 local commits ahead).
- **Affected Surfaces**:
  - Desktop: Electron main process lifecycle (`apps/desktop/electron/main.ts`), project file tree sync and tests (`use-project-tree.ts`, `use-project-tree.test.ts`), model controls test harness (`use-model-controls.test.tsx`), executable branding and packaging (`set-exe-identity.mjs`).
  - Gateway & Adapters: Buzz WebSocket transport and keepalive parameters (`plugins/platforms/buzz/adapter.py`).
  - TUI & Backend: TUI prompt session turn admission and compute-host error isolation (`tui_gateway/methods_prompt.py`), ACP adapter execution iteration bounds (`acp_adapter/session.py`), MCP background startup discovery and tests (`tests/hermes_cli/test_mcp_startup.py`).
  - Dependencies: Python lockfile (`uv.lock`) and root npm dependency graph (`package-lock.json`).
- **Operational Constraints**:
  - Zero application logic changes in this specification phase by Designer.
  - No modification to live running services (Gateway, Dashboard, Desktop).
  - No direct merge to `origin/main` without Commander morning sign-off.
