## Tasks

- [x] 1.1 Macro Reconnaissance & Deprecation Audit: Record upstream commit range (207 commits ahead, `ff2d02f998f9f9e45543eea30fb935f47e4504a7`), verify divergence against base.
- [x] 1.2 OpenSpec Contract: Establish specification and tasks for `sync-upstream-main-v11`.
- [x] 1.3 Worktree Setup: Refresh worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily` and link dependencies via NTFS junctions.
- [x] 1.4 Code Conflict Reconciliation & Invariant Preservation: Auto-preserve in-tree Honcho memory provider and reconcile merge conflicts in `pyproject.toml` and `uv.lock`.
- [x] 1.5 Typecheck Verification: Run `npm run typecheck` across `apps/shared`, `apps/desktop`, and `web` (0 errors).
- [x] 1.6 Web UI Build: Compile production bundle with Vite v8.2.0 to `hermes_cli/web_dist/index.html`.
- [x] 1.7 Targeted Unit Tests: Execute pytest on invariant suites (ACP, Buzz, Flags, Coalescing, FS: 229 passed; Honcho: 465 passed).
- [x] 1.8 Desktop Packaging (INV-8): Run `build-desktop-installer.ps1` producing `Hermes-0.21.6-win-x64.exe` (121.76 MB).
- [x] 1.9 Shadow E2E Stack Test: Run `start-worktree-e2e.ps1` on port 9129 and verify health probe.
- [x] 1.10 Autonomous Publication: Commit merge (`7ef998089d0`) and push verified branch and tags to `origin/main`.
- [ ] 1.11 Live Synchronization: Fast-forward live repo `O:/workspaces/oss/hermes-agent` and pre-warm source update (Commander execution).
- [ ] 1.12 Live Service Cutover: Safely replace Gateway and Dashboard services (Commander execution).
- [ ] 1.13 Operational Sign-Off: Verify Six-Command Suite on production stack (Commander execution).
