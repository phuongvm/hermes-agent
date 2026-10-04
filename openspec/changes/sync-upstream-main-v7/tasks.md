## Tasks

- [x] 1.1 Macro Reconnaissance & Deprecation Audit: Record upstream commit range (823 commits ahead, `343500b3547e`), verify divergence against base `beca55ec990`.
- [x] 1.2 OpenSpec Contract: Establish specification and tasks for `sync-upstream-main-v7`.
- [x] 1.3 Worktree Setup: Refresh worktree `O:/workspaces/wt-hermes-sync-daily` and link dependencies via NTFS junctions.
- [x] 1.4 Code Conflict Reconciliation: Resolve 2 unmerged files (`hermes_cli/web_server_files.py`, `tests/hermes_cli/test_web_server_fs.py`) preserving INV-1..INV-9 and security gates.
- [x] 1.5 Typecheck Verification: Run `npm run typecheck` across `apps/shared` and `apps/desktop` (0 errors).
- [x] 1.6 Targeted Unit Tests: Execute pytest on invariant suites (ACP 8/8, Buzz 175/175, FS 32/32, Lifecycle 12/12, Coalescing 2/2).
- [x] 1.7 Desktop Packaging (INV-8): Run `build-desktop-installer.ps1` producing `Hermes-0.21.5-win-x64.exe` (121.71 MB).
- [x] 1.8 Shadow E2E Stack Test: Run `start-worktree-e2e.ps1` on port 9129 and verify health probe.
- [ ] 1.9 Autonomous Publication: Push verified worktree branch to `origin/main`.
- [ ] 1.10 Live Synchronization: Fast-forward live repo `O:/workspaces/oss/hermes-agent` and pre-warm source update.
- [ ] 1.11 Live Service Cutover: Safely replace Gateway and Dashboard services.
- [ ] 1.12 Operational Sign-Off: Verify Six-Command Suite on production stack.
