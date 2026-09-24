# Tasks: Evidence-Gated Upstream Main Reconciliation (v2)

All tasks belong to downstream execution (Coder), independent review (Reviewer), or verification (QA); they MUST remain uncompleted checkboxes here (`[ ]`). Completing a task in this file represents verified downstream execution, not specification authoring by Designer.

## 1. Preflight and Integration Isolation (Coder)

- [x] 1.1 Verify and record the integration environment in `.worktrees/sync-upstream-main-v2` on branch `sync/upstream-main-v2`. Record baseline commit `77c21ec55724b279dd89f7e87f58def91c72ee1d` (Option A: TS1294 & UnscopedSecretError committed on top of `43ca20a5fd25ec415315bc93ca89370d4fd872b9`) and upstream target `c661785f872b5647fbac7c138d965180783bd9af` in the resolution ledger; confirm no silent ref advancement before merge.
- [x] 1.2 Import the four OpenSpec artifacts (`proposal.md`, `design.md`, `specs/upstream-sync-preservation/spec.md`, `tasks.md`) from `openspec/changes/sync-upstream-main-v2/` into the worktree; run `openspec validate sync-upstream-main-v2 --strict` in the integration worktree before editing code.
- [x] 1.3 Record a clean working tree or document any pre-existing dirty files in the handoff. Confirm that dependency installations, build caches, and test artifacts operate in isolated directories and cannot modify the live checkout through directory junctions or symlinks.
- [x] 1.4 Initialize the resolution ledger at `openspec/changes/sync-upstream-main-v2/reviews/resolution-ledger.md` with entries for all 10 content conflict files and invariant-critical auto-merged files.

## 2. Merge Execution and Initial Conflict Triage (Coder)

- [ ] 2.1 In `.worktrees/sync-upstream-main-v2`, execute `git merge upstream/main` without `--no-commit` or blanket strategy flags; capture full stderr and stdout, exit code, and the raw conflict list into the resolution ledger.
- [ ] 2.2 Verify and map the exact 10 content conflict paths identified in the conflict matrix: `acp_adapter/session.py`, `apps/desktop/electron/main.ts`, `apps/desktop/scripts/set-exe-identity.mjs`, `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts`, `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts`, `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx`, `plugins/platforms/buzz/adapter.py`, `tests/hermes_cli/test_mcp_startup.py`, `tui_gateway/methods_prompt.py`, and `uv.lock`.

## 3. Dependency and Toolchain Reconciliation (Coder)

- [ ] 3.1 Reconcile `pyproject.toml`: preserve required local dependency extras, Python version boundaries, and pinned sub-dependencies alongside upstream package additions and CVE updates.
- [ ] 3.2 Reconcile and regenerate `uv.lock` (C10): regenerate lockfile from reconciled `pyproject.toml` using `uv lock` in an isolated environment; execute an isolated `uv sync --locked` test and record command and exit code.
- [ ] 3.3 Reconcile `apps/desktop/package.json` with root workspace manifests; preserve required Electron/React scripts, test runners, and dependency constraints without silent version rollbacks.
- [ ] 3.4 Regenerate root `package-lock.json` from reconciled manifests using designated npm toolchain; execute clean isolated install and verify no extraneous lockfile changes exist.

## 4. Invariant SYNC-3: Backend Process-Home Auth Ownership & Token Persistence (Coder)

- [ ] 4.1 Audit auto-merged `hermes_cli/dashboard_auth/routes.py`: verify process-home binding and canonical provider selection under D2/D6; confirm off-event-loop refresh singleflighting does not restore request-scoped session resolution.
- [ ] 4.2 Audit auto-merged `hermes_cli/dashboard_auth/login_page.py`: verify native provider chooser HTML renderer preserves proper escaping, PKCE input parameters, and state preservation.
- [ ] 4.3 Audit auto-merged `hermes_cli/web_server.py`: verify host-auth provider initialization, persistent token resolution (`.dashboard_session_token`), restrictive file permissions, environment variable precedence, and the 503 `Retry-After: 3` startup grace period.
- [ ] 4.4 Audit `plugins/dashboard_auth/self_hosted/` and auth middleware: confirm immutable host-auth binding under root and named-profile process launches, and verify that request-scoped rediscovery cannot replace active host provider.
- [ ] 4.5 Execute backend auth test suites (`tests/hermes_cli/test_dashboard_auth*.py`, including `test_dashboard_auth_native_flow.py`); record test commands, outputs, and exit codes in resolution ledger.

## 5. Invariants SYNC-4 & SYNC-5: Desktop Native 401 Recovery & Reconnect Resilience (Coder)

- [ ] 5.1 Reconcile `apps/desktop/electron/main.ts` (C02): maintain single-replay execution (`executeWithNativeBearerSingleReplay`), force-refresh triggers, rejected-bearer matching, and generation tracking; wire terminal-auth suspension without cascading unauthenticated requests; integrate upstream screenshot/discovery/exit-recovery hooks.
- [ ] 5.2 Audit auto-merged `apps/desktop/electron/native-auth-decisions.ts`: verify single-replay decision logic, unexpired 401 force-refresh handling, and accurate HTTP status propagation without introducing nested retry loops.
- [ ] 5.3 Audit auto-merged `apps/desktop/src/store/profile.ts`: verify disconnected cache retention, error absorption during reconnect, immediate terminal refresh suspension, and coalesced refresh on reconnect.
- [ ] 5.4 Reconcile `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts` (C05): preserve local SYNC-5 guards (`isTerminalSignedOut` check in `loadRoot`/`refreshRoot`/`loadChildren` and `registerResumeSyncHandler` effect); integrate upstream `showIgnored` toggle and `showsIgnoredFiles` snapshotting.
- [ ] 5.5 Reconcile `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts` (C04): merge local terminal signed-out suppression and resume sync test cases with upstream show-ignored preference test cases.
- [ ] 5.6 Reconcile `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx` (C06): merge test harness with upstream `confirmMock` and `@/i18n` updates while preserving local gateway state setup and `@/hermes` mock.
- [ ] 5.7 Audit auto-merged Desktop auth and resilience files: `auth-terminal-state.ts`, preload/IPC bridges, API client, and renderer background sync hooks; confirm single-global-reauth-modal behavior across pooled connections.
- [ ] 5.8 Execute Desktop test suites and typechecks in `apps/desktop`: run affected Vitest suites (native auth, profile store, project tree, model controls) and `tsc --noEmit` across all 3 tsconfigs; record outputs in resolution ledger.

## 6. Invariant SYNC-6: Gateway Base & Buzz Platform Tuning (Coder)

- [ ] 6.1 Reconcile `plugins/platforms/buzz/adapter.py` (C07): strictly preserve local keepalive tuning constants (`_DEFAULT_WS_OPEN_TIMEOUT = 30.0`, `_DEFAULT_WS_PING_INTERVAL = 30.0`, `_DEFAULT_WS_PING_TIMEOUT = 60.0`, `SUPPORTS_MESSAGE_EDITING = False`); integrate upstream `_consume_ws_read_task` and `ConnectionClosed` exception handling in `_ws_discovery_loop`.
- [ ] 6.2 Audit auto-merged `gateway/platforms/base.py` and `gateway/run.py`: confirm that Buzz keepalive parameters remain intact and non-editable final delivery executes cleanly without duplicate preview frames.
- [ ] 6.3 Execute gateway and platform test suites (`tests/gateway/test_buzz_*.py`, 266 tests); record commands, outputs, and exit codes in resolution ledger.

## 7. Invariant SYNC-7: Multi-Provider JWKS Classification & Native Chooser (Coder)

- [ ] 7.1 Verify self-hosted OIDC verifier retains JWKS key classification: tokens with unknown `kid` in valid reachable JWKS are classified as unverifiable (permitting fallback to next provider) rather than an outage.
- [ ] 7.2 Verify `/auth/native/authorize` multi-provider chooser: confirm that omitted `provider` with multiple brokerable providers renders chooser; single eligible provider auto-selects; verify PKCE and loopback redirect security checks remain intact.
- [ ] 7.3 Execute multi-provider verification tests covering foreign key ID pass-through, broken JWKS failure handling, and interactive chooser links; record all outputs in resolution ledger.

## 8. Cataloged Content Conflict Resolution (Coder)

- [ ] 8.1 Reconcile `acp_adapter/session.py` (C01): preserve local `max_iterations` config resolution (`HERMES_MAX_ITERATIONS`, `config.acp.max_iterations`, fallback 90) and pass to `AIAgent` kwargs; integrate upstream `cwd` in kwargs and `target_model` in `resolve_runtime_provider`. Verify with ACP session test.
- [ ] 8.2 Reconcile `apps/desktop/scripts/set-exe-identity.mjs` (C03): preserve local fail-closed SemVer validation and 16-bit tuple validation in `buildRceditOptions()`; integrate upstream AV/EDR retry loop (`RCEDIT_COMMIT_RETRY_DELAYS_MS`). Verify packaging and branding script tests.
- [ ] 8.3 Reconcile `tests/hermes_cli/test_mcp_startup.py` (C08): update `_install_retry_stubs` with upstream `status` parameter; retain upstream lazy-only discovery test; preserve local disabled-server tests. Verify pytest `test_mcp_startup.py`.
- [ ] 8.4 Reconcile `tui_gateway/methods_prompt.py` (C09): preserve local compute-host error cleanup (releasing running flag, clearing inflight turn under history lock) and 4092 pre-existing session error; integrate upstream `display_kind` param, `_persist_submit_user_row`, and `_session_turn_admission` (5035). Verify TUI gateway prompt tests.

## 9. Independent Review & Invariant Audit (Reviewer)

- [x] 9.1 Review complete resolution ledger (`resolution-ledger.md`) against all 10 content conflicts and invariant-critical auto-merged dependencies.
- [x] 9.2 Audit source diffs for each of the five non-negotiable invariants (SYNC-3 through SYNC-7): confirm no behavioral regression, no blanket `ours`/`theirs` selection, and every upstream replacement is justified by line-by-line evidence.
- [x] 9.3 Perform independent test runs on candidate commit; produce `findings-reviewer-v1.md` with explicit line citations, test commands, and reproducible verdicts.

## 10. Zero-Trust QA Verification & Handoff (QA)

- [x] 10.1 Verify exact candidate commit identity and clean status in `.worktrees/sync-upstream-main-v2`; confirm candidate matches Reviewer audit target.
- [x] 10.2 Execute complete regression test matrix across Python backend, Gateway, and Desktop (Vitest + typecheck + build); verify all tests pass with zero skips or failures in critical suites.
- [x] 10.3 Verify live production services, `main` branch, and shared databases were not touched during testing; confirm dependency isolation.
- [x] 10.4 Produce `verification-report.md` with raw tool outputs, command transcripts, and final gate status for Commander morning review.
