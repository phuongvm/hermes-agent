## Tasks

- [x] 1.1 Macro Reconnaissance & Deprecation Audit: Record upstream commit range (490 commits ahead, `865ba906c1a8d93de65839ee7af487204d42e873`), verify divergence against base `dee296f1c1a2`.
- [x] 1.2 OpenSpec Contract: Establish specification and tasks for `sync-upstream-main-v8`.
- [x] 1.3 Worktree Setup: Refresh worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily` and link dependencies via NTFS junctions.
- [x] 1.4 Code Conflict Reconciliation: Resolve 1 unmerged file (`web/src/components/ChatSessionList.test.tsx`) preserving INV-1..INV-9 and strict TypeScript types.
- [x] 1.5 Typecheck Verification: Run `npm run typecheck` across `apps/shared`, `apps/desktop`, and `web` (0 errors).
- [x] 1.6 Targeted Unit Tests: Execute pytest on invariant suites (ACP, Buzz, Flags, Coalescing, FS: 229 passed; Honcho: 465 passed).
- [x] 1.7 Desktop Packaging (INV-8): Run `build-desktop-installer.ps1` producing `Hermes-0.21.5-win-x64.exe` (121.75 MB).
- [x] 1.8 Shadow E2E Stack Test: Run `start-worktree-e2e.ps1` on port 9129 and verify health probe.
- [x] 1.9 Autonomous Publication: Commit merge (`b5d97dd8b02`) and push verified branch and release tags to `origin/main`.
- [ ] 1.10 Live Synchronization: Fast-forward live repo `O:/workspaces/oss/hermes-agent` and pre-warm source update (Commander execution).
- [ ] 1.11 Live Service Cutover: Safely replace Gateway and Dashboard services (Commander execution).
- [ ] 1.12 Operational Sign-Off: Verify Six-Command Suite on production stack (Commander execution).
