# Proposal: Evidence-Gated Upstream Main Reconciliation

## Why

The local fork is 175 commits unique versus 2,704 unique upstream commits at the pinned revisions below. Integrating upstream without a behavioral preservation contract risks reintroducing cross-profile authentication faults, Desktop 401 cascades, and Buzz connection failures; textual conflict resolution alone cannot prove safety.

## What Changes

- Reconcile upstream into the isolated `sync/upstream-main` integration worktree, never into live `main` during this change's implementation/review phases.
- Require a per-file resolution ledger and empirical before/after evidence for every replacement of a protected local fix. Prefer integrating upstream structure with preserved local behavior over blanket `ours`/`theirs` selection.
- Preserve five invariant groups: process-home OIDC ownership; native force-refresh and single replay; reconnect/terminal-auth suspension; Buzz keepalive and non-editing delivery; multi-provider JWKS classification and provider chooser.
- Catalog the 14 supplied conflict paths, three ancillary dependency paths, and the independently observed conflict inventory. Treat clean auto-merges in invariant-critical dependencies as review targets, not proof of compatibility.
- Reconcile package manifests and lockfiles using isolated dependency installations, followed by affected tests, Desktop typecheck/build, independent Reviewer audit, and QA's exact-candidate verification.
- Stop before deployment, live service restart, main-branch integration, or archive; Commander approval remains required.

## Capabilities

### New Capabilities

- `upstream-sync-preservation`: An evidence-gated integration contract covering isolated reconciliation, inventory completeness, preservation of existing authentication/resilience behavior, reproducible dependencies, and independent acceptance.

### Modified Capabilities

None. This change preserves rather than weakens existing requirements in `dashboard-auth`, `desktop-reconnect-resilience`, `buzz-websocket`, `remote-fs-security`, and `desktop-remote-file-preview`. The active `desktop-native-401-single-replay` and `fix-multi-provider-jwks-kid-classification` deltas remain explicit preservation inputs, not silently archived or duplicated into their main specs here. The new integration spec defines acceptance of the merge, not replacement product APIs.

## Impact

- Local baseline: `c57316beafaba4e27b9797e137430619f98a899d`.
- Upstream target: `24fd22b94df040d843eb280ff197a4bcd99a6fc3` (observed `upstream/main`; do not silently advance this ref).
- Designer artifacts are authored in `.worktrees/t_b79cb241` on `wt/t_b79cb241`; Coder's merge belongs exclusively in `.worktrees/sync-upstream-main` on `sync/upstream-main`. Coder must import and hash-verify this artifact set before applying it; worktree files do not propagate automatically.
- Affected surfaces: Electron auth/IPC, renderer state/hooks, dashboard providers/middleware/routes, gateway adapter delivery, dependencies and documentation. Newly observed conflicts also involve Kanban persistence, MCP discovery, and filesystem security; see design inventory and preflight scope gate.
- No new endpoint, credential format, database schema, or external service is designed by this proposal. Upstream schema changes must be tested against disposable copies and must not be applied to production databases during verification.
- Authorized context substitutes: task invariants plus canonical specs for the absent `REQUIREMENTS.md`; root/area `AGENTS.md` for absent `ARCHITECTURE.md` (Leader authorization on Kanban). The named `system-architecture.md` is also absent at local HEAD; `website/docs/developer-guide/architecture.md` was read as supporting current orientation, not claimed to be that missing file.
- Evidence: `git rev-list --left-right --count HEAD...upstream/main` returned `175 2704`; `git merge-tree --write-tree --name-only HEAD upstream/main` returned 14 conflict paths and exit 1. The actual set differs from the task's supplied set; both are retained in `design.md` without asserting absent files exist.
