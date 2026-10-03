## Tasks

- [x] 1.1 Macro Reconnaissance & Deprecation Audit: Record upstream commit range, divergence, and Honcho deletion impact.
- [x] 1.2 OpenSpec Contract: Establish specification and tasks for `sync-upstream-main-v6`.
- [x] 1.3 Worktree Setup: Refresh worktree `O:/workspaces/wt-hermes-sync-daily` and link dependencies via NTFS junctions.
- [x] 1.4 Code Conflict Reconciliation: Resolve 21 unmerged files preserving INV-1..INV-9 and in-tree Honcho memory provider.
- [x] 1.5 Typecheck Verification: Run `npm run typecheck` across `apps/shared` and `apps/desktop`.
- [x] 1.6 Targeted Unit Tests: Execute pytest on invariant suites (ACP, Buzz, Coalescing, Doctor).
- [x] 1.7 Desktop Packaging (INV-8): Run `build-desktop-installer.ps1` to produce `Hermes-<version>-win-x64.exe` (>110MB).
- [x] 1.8 Shadow E2E Stack Test: Run `start-worktree-e2e.ps1` on port 9129 and verify `/api/status` and `/api/profiles`.
- [x] 1.9 Autonomous Publication: Push verified worktree branch to `origin/main`.
- [ ] 1.10 Live Synchronization: Fast-forward live repo `O:/workspaces/oss/hermes-agent` and pre-warm source update.
- [ ] 1.11 Live Service Cutover: Safely replace Gateway and Dashboard services.
- [ ] 1.12 Operational Sign-Off: Verify Six-Command Suite on production stack.
