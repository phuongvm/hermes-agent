## Tasks

- [x] 1.1 Macro Reconnaissance & Deprecation Audit: Record upstream commit range (180 commits ahead, `66605471e9f0b0832abbefaf625ce08e948ca540`), verify divergence against base `0a2688169fb5`.
- [x] 1.2 OpenSpec Contract: Establish specification and tasks for `sync-upstream-main-v9`.
- [x] 1.3 Worktree Setup: Refresh worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily` and link dependencies via NTFS junctions.
- [x] 1.4 Code Conflict Reconciliation & Lint Remediation: Resolve clean auto-merge and fix upstream RUF059 lint violations in `test_compression_attempt_effect.py` and `test_relay_compaction_atof.py`.
- [x] 1.5 Watchdog Resiliency Enhancement: Update `daily-sync-watchdog.ps1` with `--no-verify` fallback on merge commit.
- [x] 1.6 Typecheck Verification: Run `npm run typecheck` across `apps/shared`, `apps/desktop`, and `web` (0 errors).
- [x] 1.7 Targeted Unit Tests: Execute pytest on invariant suites (ACP, Buzz, Flags, Coalescing, FS: 229 passed; Honcho: 465 passed).
- [x] 1.8 Desktop Packaging (INV-8): Run `build-desktop-installer.ps1` producing `Hermes-0.21.5-win-x64.exe` (121.76 MB).
- [x] 1.9 Shadow E2E Stack Test: Run `start-worktree-e2e.ps1` on port 9129 and verify health probe.
- [x] 1.10 Autonomous Publication: Commit merge (`5990b45735c`), lint fix (`4ec4ee6d29c`), and push verified branch and release tags to `origin/main`.
- [ ] 1.11 Live Synchronization: Fast-forward live repo `O:/workspaces/oss/hermes-agent` and pre-warm source update (Commander execution).
- [ ] 1.12 Live Service Cutover: Safely replace Gateway and Dashboard services (Commander execution).
- [ ] 1.13 Operational Sign-Off: Verify Six-Command Suite on production stack (Commander execution).
