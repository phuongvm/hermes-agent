# Upstream Reconciliation Resolution Ledger (v2)

## 1. Provenance & Preflight
- **Worktree**: `O:/workspaces/oss/hermes-agent/.worktrees/sync-upstream-main-v2`
- **Branch**: `sync/upstream-main-v2`
- **Local Base**: `77c21ec55724b279dd89f7e87f58def91c72ee1d` (Option A: TS1294 & UnscopedSecretError committed on top of `43ca20a5fd25ec415315bc93ca89370d4fd872b9`)
- **Upstream Target**: `c661785f872b5647fbac7c138d965180783bd9af` (`upstream/main`, ahead by 1,360 commits)
- **Common Merge-Base**: `24fd22b94df040d843eb280ff197a4bcd99a6fc3`
- **Divergence Metrics**: 181 local commits ahead, 1,360 upstream commits ahead (`181 1360`)
- **Preflight State**: Clean working tree; OpenSpec change `sync-upstream-main-v2` validated strictly (`exit 0`).

---

## 2. Invariant Preservation Audit Matrix

| Invariant | Description | Preservation Status | Evidence & Test Suite |
| :--- | :--- | :--- | :--- |
| **SYNC-3: Backend Process-Home Auth Ownership** | Process-home auth provider registration, session token persistence (`.dashboard_session_token`), startup grace 503 `Retry-After: 3` | VERIFIED | `routes.py`, `web_server.py` diff clean against base; 503 grace and token intact |
| **SYNC-4: Desktop Native Bearer 401 Recovery** | Single-replay execution (`executeWithNativeBearerSingleReplay`), force-refresh on unexpired token, rejected-bearer matching | VERIFIED | `native-auth-decisions.ts` (diff clean), `main.ts` L16062; Vitest native-auth 31/31 passed |
| **SYNC-5: Terminal-Auth Suspension & Resilience** | Disconnected cache retention, immediate terminal-auth suspension (`isTerminalSignedOut`), resume sync handler | VERIFIED | `main.ts` L8093/L16041, `profile.ts`, `use-project-tree.ts`; Vitest 26/26 passed |
| **SYNC-6: Buzz Keepalive Tuning & Non-Editing** | WAN keepalive timeouts (30s interval / 60s timeout), `SUPPORTS_MESSAGE_EDITING = False` | VERIFIED | `plugins/platforms/buzz/adapter.py` L144-146, L513; pytest 26/26 passed |
| **SYNC-7: Multi-Provider JWKS & Native Chooser** | Foreign `kid` classified as unverifiable, PKCE challenge/method validation, native brokerable chooser | VERIFIED | `routes.py`, `login_page.py` diff clean; native chooser HTML renderer intact |
| **SYNC-8: Dependency & Toolchain Isolation** | Reproducible `uv.lock` & `package-lock.json` generation, isolated build & test environments | VERIFIED | `uv lock --check` clean (exit 0); Desktop `npm run typecheck` clean (exit 0) |

---

## 3. Conflict Resolution Matrix (10 Files)

| ID | File Path | Conflict Summary | Preservation Strategy | Verification & Status |
|:---:|:---|:---|:---|:---:|
| **C01** | `acp_adapter/session.py` | Kwargs divergence (`max_iterations` vs `cwd` & `target_model`) | Preserve local `max_iterations` config resolution (fallback 90); pass to AIAgent; incorporate upstream `cwd` and `target_model`. | VERIFIED (L398-406) |
| **C02** | `apps/desktop/electron/main.ts` | Auth recovery coordinator vs upstream screenshot/discovery/exit hooks | Preserve `executeWithNativeBearerSingleReplay`, `ensureNativeAccessToken`, `reauthModalLatch`; integrate upstream lifecycle hooks without nesting retry loops. | VERIFIED (L288, L8093, L16062) |
| **C03** | `apps/desktop/scripts/set-exe-identity.mjs` | Fail-closed SemVer & 16-bit tuple validation vs AV/EDR retry loop | Retain local fail-closed SemVer parsing & tuple validation in `buildRceditOptions()`; wrap in upstream retry loop `RCEDIT_COMMIT_RETRY_DELAYS_MS`. | VERIFIED (L14, L129-182) |
| **C04** | `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts` | Terminal signed-out suppression tests vs upstream show-ignored tests | Union test suites: retain all local SYNC-5 terminal suppression & resume sync tests; incorporate upstream show-ignored tests. | VERIFIED (Vitest 26/26 passed) |
| **C05** | `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts` | `isTerminalSignedOut` guards & resume sync vs `showIgnored` toggle | Preserve local SYNC-5 guards on `loadRoot`/`refreshRoot`/`loadChildren` & `registerResumeSyncHandler`; adopt upstream `showIgnored` toggle & snapshot. | VERIFIED (L68, L78, L90) |
| **C06** | `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx` | Gateway open/idle setup & mocks vs upstream `confirmMock` & `@/i18n` | Merge test harness: adopt upstream `confirmMock` & `@/i18n`; preserve local gateway state setup & `@/hermes` mock; retain all assertions. | VERIFIED (Vitest 25/25 passed) |
| **C07** | `plugins/platforms/buzz/adapter.py` | Keepalive constants & non-editing vs upstream read task cleanup | Strictly preserve local timeouts (30s interval / 60s timeout) and `SUPPORTS_MESSAGE_EDITING = False`; integrate `_consume_ws_read_task` and `ConnectionClosed` handling. | VERIFIED (pytest 26/26 passed) |
| **C08** | `tests/hermes_cli/test_mcp_startup.py` | Disabled MCP server tests vs upstream `status` parameter & lazy test | Union both: update `_install_retry_stubs` with `status: str = "configured"`; keep upstream lazy discovery test; retain local disabled-server tests. | VERIFIED (pytest 19/19 passed) |
| **C09** | `tui_gateway/methods_prompt.py` | Compute-host error cleanup (4092) vs upstream `display_kind` & 5035 | Retain local error cleanup & 4092 pre-existing session error; integrate upstream `display_kind`, `_persist_submit_user_row`, and `_session_turn_admission` (5035). | VERIFIED (L101, L462, L657) |
| **C10** | `uv.lock` | Merge conflict in locked packages | Do not resolve textually. Reconcile `pyproject.toml`, regenerate via `uv lock`, verify with `uv sync --locked`. | VERIFIED (`uv lock --check` exit 0) |

---

## 4. Invariant-Critical Auto-Merged Dependencies (Mandatory Audit)

| File Path | Invariant | Audit Focus | Audit Status |
|:---|:---:|:---|:---:|
| `hermes_cli/dashboard_auth/routes.py` | **SYNC-3**, **SYNC-7** | Process-home binding & canonical chooser eligibility | AUDITED (Diff clean vs base) |
| `hermes_cli/dashboard_auth/login_page.py` | **SYNC-3**, **SYNC-7** | Native chooser HTML renderer, PKCE fields, escaping | AUDITED (Diff clean vs base) |
| `hermes_cli/web_server.py` | **SYNC-3** | Host-auth init, token persistence precedence, 503 grace | AUDITED (503 grace & token intact) |
| `apps/desktop/electron/native-auth-decisions.ts` | **SYNC-4** | Single-replay decision logic, unexpired 401 force-refresh | AUDITED (Diff clean vs base) |
| `apps/desktop/src/store/profile.ts` | **SYNC-5** | Disconnected cache retention, terminal-auth suspension | AUDITED (Suppression & cache intact) |
| `gateway/platforms/base.py` | **SYNC-6** | Non-editing platform delivery contract | AUDITED (Contract intact) |
| `gateway/run.py` | **SYNC-6** | Final turn delivery for non-editing adapters | AUDITED (Contract intact) |
| `tests/hermes_cli/test_dashboard_auth_native_flow.py` | **SYNC-7** | Test coverage for native chooser & token routing | AUDITED (Flow tests intact) |
