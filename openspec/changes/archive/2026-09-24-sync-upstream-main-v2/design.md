# Design: Evidence-Gated Upstream Main Reconciliation (v2)

## Context

This design establishes the technical architecture and conflict resolution rules for reconciling upstream changes into the local fork. Designer task `t_e55b28a0` produces the OpenSpec specification artifacts; downstream implementation (`coder`), review (`reviewer`), and verification (`qa`) operate under this contract.

Pinned input revisions:
- **Local Baseline (L)**: `43ca20a5fd25ec415315bc93ca89370d4fd872b9` (`origin/main`).
- **Upstream Target (U)**: `c661785f872b5647fbac7c138d965180783bd9af` (`upstream/main`).
- **Common Merge-Base (MB)**: `24fd22b94df040d843eb280ff197a4bcd99a6fc3`.
- **Divergence Metrics**: `git rev-list --left-right --count HEAD...upstream/main` yields `180 1360` (180 local commits ahead, 1,360 upstream commits ahead).
- **Merge-Tree Preview**: `git merge-tree --write-tree HEAD upstream/main` yields exit code 1 with preview tree `5dc73cff22712b21878cd86fec2018e4d77968b9` and isolates exactly 10 content conflict paths.

Context substitutes authorized per collaboration protocol: task invariants and canonical specs (`openspec/specs/dashboard-auth/spec.md`, `desktop-reconnect-resilience/spec.md`, `buzz-websocket/spec.md`) serve as requirements source; root and area `AGENTS.md` files serve as architectural guide.

## Goals / Non-Goals

**Goals:**
- Strictly preserve all five non-negotiable invariant groups (SYNC-3 through SYNC-7) across dashboard auth, Desktop 401 single-replay, reconnect resilience, Buzz WebSocket keepalive, and multi-provider JWKS kid classification.
- Catalog all 10 content conflict files with root-cause analysis, diff mapping, and explicit preservation strategies.
- Enforce semantic review on all invariant-critical auto-merged files to guarantee no local security or resilience logic is silently clobbered.
- Provide a clear verification plan with concrete test selectors and an empirical resolution ledger (`resolution-ledger.md`).

**Non-Goals:**
- Application coding by Designer (strictly forbidden by domain boundaries).
- Blanket acceptance of upstream files (`ours` or `theirs` shortcuts).
- Direct modification or commits to live `origin/main`.
- Touching live running services (Gateway, Dashboard, Desktop) or shared databases.
- Premature archive of this or prerequisite changes without Commander authorization.

## Decisions

### D1. Preserve behavior, not entire sides of conflicted files
Never resolve a conflict using blanket `--ours` or `--theirs`. Where upstream refactors structure (e.g. `main.ts`, `use-project-tree.ts`, `adapter.py`), transplant preserved local semantics at their new call sites. Where both sides add orthogonal capabilities (e.g. test suites in `use-project-tree.test.ts`, `use-model-controls.test.tsx`, `test_mcp_startup.py`), form the union of both additions. Clean auto-merges in invariant-critical dependencies must also undergo semantic audit.

### D2. Host process-home authentication ownership survives reconciliation (SYNC-3)
Preserve the immutable binding of host auth provider registration and session storage to the process home resolved at server boot. Request-selected profile context (via gateway routing or multiplexing) may direct agent config and MCP discovery, but MUST NOT redirect host auth session storage or swap host providers. Retain `web_server.py` startup token persistence, restrictive token file permissions, and the 503 `Retry-After: 3` startup grace period.

### D3. One native 401 recovery boundary (SYNC-4)
`apps/desktop/electron/main.ts` must route native bearer 401 recovery exclusively through one shared coordinator (`executeWithNativeBearerSingleReplay` / `native-auth-decisions.ts`). Retain force-refresh on unexpired bearer, rejected-bearer matching, generation tracking, and at most one replay per original request. Upstream additions (`cloud-discovery`, `command-screenshot`, `backend-exit-recovery`) must be integrated into main lifecycle without nesting or bypassing local auth recovery loops.

### D4. Reconnect and terminal rejection are distinct states (SYNC-5)
Preserve `desktop-reconnect-resilience` behavior in renderer and project tree:
1. Disconnected state: suppress background requests (`loadRoot`, `loadChildren`, model/profile refreshes) and retain cached data without unhandled promise rejections.
2. Terminal signed-out state: when current native refresh returns an authoritative terminal 401, atomically transition endpoint to signed-out / reauth-required, halt polling/retry timers, and display reauth modal.
3. Resumption: on confirmed sign-in, clear signed-out state and resume deferred work once per data source (via `registerResumeSyncHandler`).
4. Keep the 2-in-15s consecutive-401 debounce policy for ordinary health probes completely separate from immediate terminal refresh rejection.

### D5. Buzz WebSocket liveness and non-editing delivery survive reconciliation (SYNC-6)
Retain local keepalive tuning constants in `plugins/platforms/buzz/adapter.py`:
- `_DEFAULT_WS_OPEN_TIMEOUT = 30.0`
- `_DEFAULT_WS_PING_INTERVAL = 30.0`
- `_DEFAULT_WS_PING_TIMEOUT = 60.0`
- `SUPPORTS_MESSAGE_EDITING = False`
Integrate upstream's detached stalled receive task cleanup (`_consume_ws_read_task`) and `ConnectionClosed` handling in `_ws_discovery_loop` without altering the keepalive timeouts or re-enabling message editing on Nostr Kind 9.

### D6. Distinguish foreign JWKS keys from provider outages; preserve native chooser (SYNC-7)
Retain self-hosted OIDC verifier logic: an unmatched `kid` against reachable, valid JWKS classifies the token as unverifiable for that provider (allowing fallback to the next provider), rather than an outage. In `routes.py` and `login_page.py`, preserve the native brokerable chooser HTML renderer, PKCE challenge/method validation, loopback redirect protection, and automatic single-provider selection.

### D7. Dependency and lockfile isolation (SYNC-8)
Do not edit lockfiles (`uv.lock`, `package-lock.json`) manually. Reconcile package manifests (`pyproject.toml`, `package.json`) first, then regenerate lockfiles using designated tools in isolated environments. Verify with clean installs.

### D8. ACP adapter iteration limits configuration
In `acp_adapter/session.py`, preserve local iteration limits resolution (`HERMES_MAX_ITERATIONS` env var, `config.acp.max_iterations`, `agent.max_iterations`/`max_turns`, fallback 90) and pass `max_iterations: int(max_iter)` to `AIAgent` kwargs. Combine with upstream's `cwd` parameter in kwargs and `target_model` resolution in `resolve_runtime_provider`.

### D9. TUI prompt session turn admission and compute-host error isolation
In `tui_gateway/methods_prompt.py`, preserve local compute-host error cleanup (releasing running flag and clearing inflight turn under history lock on dispatch failure, plus 4092 validation error when turn isolation is enabled for pre-existing in-process sessions). Integrate upstream's `display_kind` parameter and `_session_turn_admission(session)` check for backend retirement (5035).

### D10. Fail-closed PE identity stamping with transient lock retry
In `apps/desktop/scripts/set-exe-identity.mjs`, preserve local fail-closed behavior (throwing on packaging stamp failures instead of silently swallowing), SemVer parsing, and 16-bit tuple validation in `buildRceditOptions()`. Integrate upstream's retry loop (`RCEDIT_COMMIT_RETRY_DELAYS_MS = [500, 1000, 2000]`) to handle transient file-lock contention from real-time AV/EDR scanners.

---

## Conflict Resolution Matrix

The 10 content conflict files identified by `git merge-tree` between L (`43ca20a5fd`) and U (`c661785f87`):

| ID | File Path | Conflict Type & Hunks | Root Cause & Divergence | Invariant / Contract | Preservation Strategy & Evidence |
|:---:|:---|:---:|:---|:---:|:---|
| **C01** | `acp_adapter/session.py` | Content (kwargs dict) | Local adds `max_iterations` config resolution; upstream adds `cwd` in kwargs and `target_model` in runtime provider resolution. | Agent Execution Bounds | Combine both: retain local `max_iterations` extraction and pass in kwargs; adopt upstream `cwd` in kwargs and `target_model` in `resolve_runtime_provider`. Verify with ACP session initialization test. |
| **C02** | `apps/desktop/electron/main.ts` | Content (multiple hunks) | Local implements P0 native single-replay, reauth modal latch, and terminal auth state; upstream adds `cloud-discovery`, `command-screenshot`, and `backend-exit-recovery`. | **SYNC-4**, **SYNC-5** | Preserve local `executeWithNativeBearerSingleReplay`, `ensureNativeAccessToken`, `reauthModalLatch`, and terminal state listeners. Wire upstream screenshot/discovery/exit-recovery hooks around local auth coordinators. Verify Vitest native-auth (31 tests) and IPC tests. |
| **C03** | `apps/desktop/scripts/set-exe-identity.mjs` | Content (options & retry) | Local enforces fail-closed SemVer & 16-bit tuple validation; upstream adds AV/EDR retry loop for file locks. | Packaging & Branding | Merge both: keep local fail-closed SemVer parsing and 16-bit tuple validation in `buildRceditOptions()`; wrap `runRcedit` in upstream retry loop with `RCEDIT_COMMIT_RETRY_DELAYS_MS`. Verify unit test and packaging scripts. |
| **C04** | `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts` | Content (appended tests) | Local appends tests for terminal signed-out suppression & resume sync; upstream appends tests for `$showIgnoredRoots` preference flip. | **SYNC-5** | Union test suites: retain all local terminal signed-out suppression and resume-sync tests (SYNC-5); incorporate upstream show-ignored tests. Verify Vitest `use-project-tree.test.ts` passes 100%. |
| **C05** | `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts` | Content (guards & return type) | Local adds `isTerminalSignedOut` guards on `loadRoot`/`refreshRoot`/`loadChildren` & `registerResumeSyncHandler`; upstream adds `showIgnored` toggle & read consistency check. | **SYNC-5** | Preserve local SYNC-5 guards (`isTerminalSignedOut` check in `loadRoot`/`refreshRoot`/`loadChildren` and `registerResumeSyncHandler` effect); adopt upstream `showIgnored` state, return props, and `showsIgnoredFiles` snapshotting. Verify typecheck and tests. |
| **C06** | `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx` | Content (mocks & tests) | Local sets gateway state open/idle in beforeEach/afterEach and mocks `@/hermes`; upstream mocks `@/store/confirm` and updates model switch failure dialog tests. | Test Harness Fidelity | Merge test harness: adopt upstream `confirmMock` and `@/i18n` updates; preserve local gateway open/idle state setup and `@/hermes` importOriginal mock; retain both local and upstream test assertions. Verify Vitest suite passes. |
| **C07** | `plugins/platforms/buzz/adapter.py` | Content (constants & loops) | Local sets WAN keepalive constants (30s interval / 60s timeout) and `SUPPORTS_MESSAGE_EDITING = False`; upstream adds `_consume_ws_read_task` and `ConnectionClosed` handling in discovery loop. | **SYNC-6** | Strictly preserve local SYNC-6 timeouts (`_DEFAULT_WS_OPEN_TIMEOUT = 30.0`, `_DEFAULT_WS_PING_INTERVAL = 30.0`, `_DEFAULT_WS_PING_TIMEOUT = 60.0`, `SUPPORTS_MESSAGE_EDITING = False`); integrate upstream's `_consume_ws_read_task` and `ConnectionClosed` in `_ws_discovery_loop`. Verify Buzz test suite (266 tests). |
| **C08** | `tests/hermes_cli/test_mcp_startup.py` | Content (test helpers & tests) | Local adds tests for disabled MCP servers and skipping discovery; upstream adds `status` param in `_install_retry_stubs` and tests lazy-only discovery. | MCP Discovery | Union both: update `_install_retry_stubs` with `status: str = "configured"`; keep upstream lazy-only discovery test; preserve local disabled-server tests. Verify pytest `test_mcp_startup.py` passes 100%. |
| **C09** | `tui_gateway/methods_prompt.py` | Content (turn admission & error) | Local adds compute-host error cleanup (clearing running flag, releasing locks) and 4092 pre-existing session error; upstream adds `_persist_submit_user_row` with `display_kind` and `_session_turn_admission` (5035). | Turn Lifecycle & Compute Host | Retain local compute-host error cleanup and turn isolation check (4092); integrate upstream's `display_kind` param, `_persist_submit_user_row`, and `_session_turn_admission` (5035). Verify TUI gateway prompt tests. |
| **C10** | `uv.lock` | Content (lock packages) | Local has dependency tree for fork extras; upstream has updated dependency locks (`google-cloud-pubsub`, ordering). | **SYNC-8** | Do not resolve textually. Reconcile `pyproject.toml` (A02), then regenerate `uv.lock` using `uv lock` in an isolated environment. Verify with isolated `uv sync --locked`. |

### Invariant-Critical Auto-Merged Dependencies (Mandatory Audit)

| File Path | Invariant | Audit Focus |
|:---|:---:|:---|
| `hermes_cli/dashboard_auth/routes.py` | **SYNC-3**, **SYNC-7** | Verify process-home session store binding and canonical brokerable chooser eligibility are not overwritten by upstream singleflighting refactor. |
| `hermes_cli/dashboard_auth/login_page.py` | **SYNC-3**, **SYNC-7** | Verify native chooser HTML renderer retains PKCE input fields, state preservation, and proper parameter escaping. |
| `hermes_cli/web_server.py` | **SYNC-3** | Verify host-auth provider initialization, `.dashboard_session_token` resolution precedence, and 503 `Retry-After: 3` startup grace period remain intact. |
| `apps/desktop/electron/native-auth-decisions.ts` | **SYNC-4** | Verify single-replay decision logic (`executeWithNativeBearerSingleReplay`) and unexpired 401 force-refresh handling remain intact. |
| `apps/desktop/src/store/profile.ts` | **SYNC-5** | Verify disconnected cache retention, error absorption during reconnect, and terminal-auth suspension remain intact. |
| `gateway/platforms/base.py` | **SYNC-6** | Verify base streaming delivery honors `SUPPORTS_MESSAGE_EDITING = False` without breaking other adapters. |
| `gateway/run.py` | **SYNC-6** | Verify final-turn delivery semantics and message dispatch for non-editing adapters. |
| `tests/hermes_cli/test_dashboard_auth_native_flow.py` | **SYNC-7** | Verify test coverage for native chooser, PKCE validation, and multi-provider token routing. |

---

## Evidence and Verification Plan

All resolutions must be documented in `openspec/changes/sync-upstream-main-v2/reviews/resolution-ledger.md` by Coder before Reviewer handoff.

### Verification Gates

- **G1 (SYNC-1 / SYNC-2)**: Worktree isolation verified (`.worktrees/sync-upstream-main-v2` on `sync/upstream-main-v2`); baseline `43ca20a5fd` and target `c661785f87` confirmed; all 10 conflicts cataloged in ledger.
- **G2 (SYNC-3)**: Dashboard auth suites (`tests/hermes_cli/test_dashboard_auth*.py`, 200+ tests) pass cleanly with zero port collisions and verified process-home binding.
- **G3 (SYNC-4)**: Desktop native-auth decisions test suite (`apps/desktop/electron/native-auth-decisions.test.ts`, 31 tests) passes 100% with verified single-replay and force-refresh assertions.
- **G4 (SYNC-5)**: Desktop profile store (`profile.test.ts`, 24 tests), gateway reconnect (`gateway-reconnect.test.ts`), and project tree (`use-project-tree.test.ts`) pass 100% with confirmed terminal auth suppression.
- **G5 (SYNC-6)**: Buzz test suite (`tests/gateway/test_buzz_*.py`, 266 tests) passes 100% with preserved 30s/60s keepalive timeouts and non-editing delivery.
- **G6 (SYNC-7)**: Multi-provider JWKS kid classification tests pass with verified foreign key ID pass-through and canonical chooser links.
- **G7 (C01, C03, C08, C09)**: Unit tests for ACP adapter, set-exe-identity, MCP startup, and TUI prompt pass cleanly.
- **G8 (SYNC-8)**: Isolated lockfile sync (`uv.lock`), Desktop typecheck (`tsc --noEmit` across all 3 tsconfigs), clean build (`npm run build`), and independent Reviewer/QA reports on exact candidate commit.

---

## Migration / Handoff / Rollback

1. **Designer Publication**: Designer commits OpenSpec artifacts (`proposal.md`, `design.md`, `specs/upstream-sync-preservation/spec.md`, `tasks.md`) to `openspec/changes/sync-upstream-main-v2/` and validates strictly (`openspec validate sync-upstream-main-v2 --strict`).
2. **Coder Handoff**: Leader creates isolated worktree `.worktrees/sync-upstream-main-v2` on branch `sync/upstream-main-v2` at `43ca20a5fd`. Coder imports artifacts, verifies hashes, executes merge of `c661785f87`, resolves the 10 conflicts per matrix, populates `resolution-ledger.md`, and commits the merge candidate.
3. **Reviewer Audit**: Reviewer inspects exact candidate commit, verifies resolution ledger against all 10 conflicts and invariant-critical dependencies, and produces `findings-reviewer-v1.md`.
4. **QA Sign-Off**: QA runs full zero-trust regression test suites, validates dependency isolation, and delivers `verification-report.md`.
5. **Rollback Strategy**: If any invariant check or test suite fails, candidate remains strictly isolated in worktree. Live `origin/main` is never touched. Commander retains final authority for merge into `origin/main`.

---

## Risks / Trade-offs

- **[Risk] High divergence volume (1,360 upstream commits)** → **Mitigation**: Rely on 3-way merge-tree conflict isolation, require line-by-line justification in `resolution-ledger.md`, and enforce semantic audits on auto-merged invariant files.
- **[Risk] Desktop Electron main.ts complexity** → **Mitigation**: Isolate auth recovery coordinators (`executeWithNativeBearerSingleReplay`, `ensureNativeAccessToken`) from general window/IPC lifecycle; run Vitest native-auth test suite (31 tests) after every edit.
- **[Risk] Host environment test contamination** → **Mitigation**: Enforce hermetic test execution with isolated `HERMES_HOME`, mock ports, and `_HERMES_BEHAVIORAL_VARS` isolation in `tests/conftest.py`.
- **[Risk] Shared node_modules / lockfile drift** → **Mitigation**: Do not edit lockfiles manually; use isolated dependency directories and run clean lock regeneration via `uv lock` and `npm install` per D7 / SYNC-8.

---

## Source / Query Appendix

- Baseline commit: `git rev-parse origin/main` -> `43ca20a5fd25ec415315bc93ca89370d4fd872b9`.
- Upstream target: `git rev-parse upstream/main` -> `c661785f872b5647fbac7c138d965180783bd9af`.
- Merge-base: `git merge-base HEAD upstream/main` -> `24fd22b94df040d843eb280ff197a4bcd99a6fc3`.
- Revision count: `git rev-list --left-right --count HEAD...upstream/main` -> `180 1360`.
- Conflict preview: `git merge-tree --write-tree HEAD upstream/main` -> Tree `5dc73cff22712b21878cd86fec2018e4d77968b9`, 10 conflict files.
- Canonical specs: `openspec/specs/dashboard-auth/spec.md`, `desktop-reconnect-resilience/spec.md`, `buzz-websocket/spec.md`.
- Active delta references: `openspec/changes/desktop-native-401-single-replay/`, `openspec/changes/fix-multi-provider-jwks-kid-classification/`.
