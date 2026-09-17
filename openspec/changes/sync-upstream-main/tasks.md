# Tasks: Evidence-Gated Upstream Main Reconciliation

All tasks belong to downstream execution (Coder), independent review (Reviewer), or verification (QA); they MUST remain uncompleted checkboxes here (`[ ]`). Completing a task in this file represents verified downstream execution, not specification authoring by Designer.

## 1. Preflight and Integration Isolation (Coder)

- [ ] 1.1 Verify and record the integration environment in `.worktrees/sync-upstream-main` on branch `sync/upstream-main`. Record baseline commit `c57316beafaba4e27b9797e137430619f98a899d` and upstream target `24fd22b94df040d843eb280ff197a4bcd99a6fc3` in the resolution ledger; confirm no silent ref advancement before merge.
- [ ] 1.2 Import the four OpenSpec artifacts (`proposal.md`, `design.md`, `specs/upstream-sync-preservation/spec.md`, `tasks.md`) from `.worktrees/t_b79cb241` into `.worktrees/sync-upstream-main`; compute SHA-256 hashes, verify alignment, and run `openspec validate sync-upstream-main --strict` in the integration worktree before editing code.
- [ ] 1.3 Record a clean working tree or document any pre-existing dirty files in the handoff. Confirm that dependency installations, build caches, and test artifacts operate in isolated directories and cannot modify the live checkout through directory junctions or symlinks.
- [ ] 1.4 Present the newly discovered conflict set (`hermes_cli/kanban_db.py`, `hermes_cli/mcp_startup.py`, `hermes_cli/web_routers/files.py`, `tests/hermes_cli/test_dashboard_auth_native_flow.py`, `website/docs/developer-guide/kanban-openspec.md`) to the Leader and obtain written scope confirmation in `agent_share.md` or Kanban comments before resolving those areas.

## 2. Merge Execution and Initial Conflict Triage (Coder)

- [ ] 2.1 In `.worktrees/sync-upstream-main`, execute `git merge upstream/main` without `--no-commit` or blanket strategy flags; capture full stderr and stdout, exit code, and the raw conflict list into the resolution ledger.
- [ ] 2.2 Initialize the resolution ledger at `openspec/changes/sync-upstream-main/reviews/resolution-ledger.md` with entries for all 14 supplied paths, all observed conflicts, dependency files, and invariant-critical auto-merged files.
- [ ] 2.3 Verify the presence or relocation of all supplied paths (`apps/desktop/src/app/session/sidebar.tsx`, `website/docs/developer-guide/system-architecture.md`, `tests/hermes_cli/test_dashboard_auth.py`). Record exact replacement paths or mark absent paths with evidence from `git ls-tree` rather than creating synthetic files.

## 3. Dependency and Toolchain Reconciliation (Coder)

- [ ] 3.1 Reconcile `pyproject.toml` (A02 / C): preserve required local dependency extras, python version boundaries, and pinned sub-dependencies alongside upstream package additions and CVE updates.
- [ ] 3.2 Regenerate and verify `uv.lock` (A03 / A) from the reconciled `pyproject.toml` using the designated uv version; execute an isolated sync test and record the command and exit code.
- [ ] 3.3 Reconcile `apps/desktop/package.json` (S07 / S) with root workspace manifests; preserve required Electron/React scripts, test runners, and dependency constraints without silent version rollbacks.
- [ ] 3.4 Regenerate root `package-lock.json` (A01 / A) from reconciled manifests using the designated npm toolchain; execute a clean isolated install and verify no extraneous lockfile changes exist.

## 4. Invariant 1: Backend Process-Home Auth Ownership & Token Persistence (Coder)

- [ ] 4.1 Reconcile `hermes_cli/dashboard_auth/routes.py` (S10 / C): preserve process-home binding and canonical provider selection under D2/D6; integrate upstream off-event-loop refresh singleflighting without restoring request-scoped session resolution.
- [ ] 4.2 Reconcile `hermes_cli/dashboard_auth/login_page.py` (S11 / C): preserve the native provider chooser HTML renderer with proper escaping, PKCE inputs, and state preservation aligned with the reconciled route handler.
- [ ] 4.3 Audit auto-merged `hermes_cli/web_server.py` (S12 / S): verify host-auth provider initialization, persistent token resolution (`.dashboard_session_token`), restrictive file permissions, environment variable precedence, and the 503 `Retry-After: 3` startup grace period.
- [ ] 4.4 Audit `plugins/dashboard_auth/self_hosted/` and auth middleware: confirm immutable host-auth binding under root and named-profile process launches, and verify that request-scoped rediscovery cannot replace the active host provider.
- [ ] 4.5 Execute backend auth test suites (`tests/hermes_cli/test_dashboard_auth*.py`, including reconciled N04 `test_dashboard_auth_native_flow.py`); record test commands, outputs, and exit codes in the resolution ledger.

## 5. Invariants 2 & 3: Desktop Native 401 Recovery & Reconnect Resilience (Coder)

- [ ] 5.1 Reconcile `apps/desktop/electron/main.ts` (S01 / C): maintain single-replay execution, force-refresh triggers, rejected-bearer matching, and generation tracking; wire terminal-auth suspension without cascading unauthenticated requests.
- [ ] 5.2 Reconcile `apps/desktop/electron/native-auth-decisions.ts` (S02 / C): preserve single-replay decision logic, unexpired 401 force-refresh handling, and accurate HTTP status propagation without introducing nested retry loops.
- [ ] 5.3 Reconcile `apps/desktop/electron/native-auth-decisions.test.ts` (S03 / C): merge local and upstream test cases without deleting local invariant tests; assert call counts, changed/unchanged bearer responses, and stale generation handling.
- [ ] 5.4 Reconcile `apps/desktop/src/store/profile.ts` (S04 / C): preserve disconnected cache retention, error absorption during reconnect, immediate terminal refresh suspension, and coalesced refresh on reconnect.
- [ ] 5.5 Reconcile `apps/desktop/src/app/session/hooks/use-model-controls.ts` (S05 / C): preserve upstream model controls while guarding background refresh calls behind gateway connection state.
- [ ] 5.6 Audit auto-merged Desktop auth and resilience files: `auth-terminal-state.ts`, preload/IPC bridges, API client, and renderer background sync hooks; confirm single-global-reauth-modal behavior across pooled connections.
- [ ] 5.7 Execute Desktop test suites and typechecks in `apps/desktop`: run affected Vitest suites, Electron main typecheck, and renderer typecheck; record all outputs and exit codes in the resolution ledger.

## 6. Invariant 4: Gateway Base & Buzz Platform Tuning (Coder)

- [ ] 6.1 Reconcile `gateway/platforms/base.py` (S08 / C): retain upstream proxy/media enhancements while preserving adapter-specific capabilities; ensure `SUPPORTS_MESSAGE_EDITING = False` adapters do not rely on edit-based streaming delivery.
- [ ] 6.2 Audit `plugins/platforms/buzz/adapter.py` and auto-merged `gateway/run.py` (S09 / S): verify that Buzz keepalive timeouts, read idle bounds, and connection parameters remain intact; confirm non-editable final delivery executes exactly once.
- [ ] 6.3 Execute gateway and platform test suites (`tests/gateway/test_buzz_adapter.py`, `tests/gateway/test_*.py`); record commands, outputs, and exit codes in the resolution ledger.

## 7. Invariant 5: Multi-Provider JWKS Classification & Native Chooser (Coder)

- [ ] 7.1 Verify self-hosted OIDC verifier retains JWKS key classification: tokens with unknown `kid` in a valid reachable JWKS are classified as unverifiable (allowing subsequent providers to evaluate them) rather than raising a provider outage error.
- [ ] 7.2 Verify `/auth/native/authorize` multi-provider chooser: confirm that omitted `provider` with multiple brokerable providers renders the chooser, while a single eligible provider auto-selects; verify PKCE and loopback redirect security checks remain intact.
- [ ] 7.3 Execute multi-provider verification tests covering foreign key ID pass-through, broken JWKS failure handling, and interactive chooser links; record all outputs in the resolution ledger.

## 8. Newly Discovered Conflicts & Documentation (Coder)

- [ ] 8.1 Reconcile `hermes_cli/kanban_db.py` (N01 / C): merge worker PID/start-time fingerprints, stale claim handling, and parent gating with local structured completion handoffs and artifact staging; verify on a disposable test database.
- [ ] 8.2 Reconcile `hermes_cli/mcp_startup.py` (N02 / C): preserve per-profile discovery slots and context-copied environment/secret scopes; test concurrent profile discovery and retry on initial failure.
- [ ] 8.3 Reconcile `hermes_cli/web_routers/files.py` (N03 / C): retain centralized sensitive-path blocking across list, read, download, and data-URL endpoints while preserving authorized remote preview capabilities; test path traversal and escape vectors.
- [ ] 8.4 Reconcile `tests/hermes_cli/test_dashboard_auth_native_flow.py` (N04 / C): align test assertions with the preserved canonical chooser and singleflight refresh behavior; confirm all assertions pass.
- [ ] 8.5 Reconcile documentation relocation for `website/docs/developer-guide/kanban-openspec.md` (N05 / C): relocate local content to the upstream path structure, verify internal links, and ensure build integrity.

## 9. Independent Review & Invariant Audit (Reviewer)

- [ ] 9.1 Review the complete resolution ledger (`resolution-ledger.md`) against the 14 supplied paths, all observed conflicts, and invariant-critical auto-merged files.
- [ ] 9.2 Audit source diffs for each of the five non-negotiable invariants: confirm no local behavioral regression exists, no silent `ours`/`theirs` selection was performed on critical logic, and any upstream replacement is justified by line-by-line evidence.
- [ ] 9.3 Perform independent test runs on the candidate commit/tree; produce `findings-reviewer-v1.md` with explicit line citations, test commands, and reproducible verdicts.

## 10. Zero-Trust QA Verification & Handoff (QA)

- [ ] 10.1 Verify exact candidate commit identity and dirty status in `.worktrees/sync-upstream-main`; confirm candidate matches Reviewer audit target.
- [ ] 10.2 Execute the complete regression test matrix across Python backend, Gateway, and Desktop (Vitest + typecheck + build); verify all tests pass with zero skips or failures in critical suites.
- [ ] 10.3 Verify that live production services, `main` branch, and shared databases were not touched during testing; confirm dependency isolation.
- [ ] 10.4 Produce `verification-report.md` with raw tool outputs, command transcripts, and final gate status for Commander morning review.
