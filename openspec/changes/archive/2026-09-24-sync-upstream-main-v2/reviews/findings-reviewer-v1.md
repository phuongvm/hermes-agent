# Independent Code Review Report (v1)

**Change ID**: `sync-upstream-main-v2`  
**Reviewer**: Agent Reviewer (`reviewer`)  
**Domain**: Code Quality & Invariant Preservation  
**Candidate Commit**: `8c56beb8b4596b4686799d1bfbb0a5456232e20a`  
**Integration Worktree**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2` (`wt/review-sync-v2`)  
**Base Revisions**: Local Baseline `77c21ec55724b279dd89f7e87f58def91c72ee1d`, Upstream Target `c661785f872b5647fbac7c138d965180783bd9af`  
**Verdict**: **APPROVED FOR DOWNSTREAM QA**  

---

## 1. Scope

The review evaluated the merge reconciliation between local baseline `77c21ec557` (Option A committed) and upstream target `c661785f87` (1,360 commits ahead).

### Evaluated Files & Components
1. **Cataloged Content Conflict Files (10/10)**:
   - `acp_adapter/session.py` (C01)
   - `apps/desktop/electron/main.ts` (C02 - SYNC-4, SYNC-5)
   - `apps/desktop/scripts/set-exe-identity.mjs` (C03)
   - `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts` (C04 - SYNC-5)
   - `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts` (C05 - SYNC-5)
   - `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx` (C06)
   - `plugins/platforms/buzz/adapter.py` (C07 - SYNC-6)
   - `tests/hermes_cli/test_mcp_startup.py` (C08)
   - `tui_gateway/methods_prompt.py` (C09)
   - `uv.lock` (C10 - SYNC-8)

2. **Invariant-Critical Auto-Merged Dependencies**:
   - `hermes_cli/dashboard_auth/routes.py` (SYNC-3, SYNC-7)
   - `hermes_cli/dashboard_auth/login_page.py` (SYNC-3, SYNC-7)
   - `hermes_cli/web_server.py` (SYNC-3)
   - `apps/desktop/electron/native-auth-decisions.ts` (SYNC-4)
   - `apps/desktop/src/store/profile.ts` (SYNC-5)
   - `gateway/platforms/base.py` (SYNC-6)
   - `gateway/run.py` (SYNC-6)
   - `tests/hermes_cli/test_dashboard_auth_native_flow.py` (SYNC-7)

3. **Core Invariant Mandates (SYNC-3 through SYNC-7)** per `specs/upstream-sync-preservation/spec.md` and `design.md`.

---

## 2. Not Reviewed

- Third-party dependency binary payloads (wheels, electron packaging pre-builts).
- Upstream feature commits outside the 10 conflict files and invariant-critical dependency boundaries (1,360 upstream commits of non-conflicting changes auto-merged cleanly by git).
- Operating system native installers outside the tested Win32 PE metadata scripts.

---

## 3. Summary of Findings

| Severity | Count | Summary |
| :--- | :---: | :--- |
| **CRITICAL** | 0 | None. No invariant regression or security/stability defect detected. |
| **WARNING** | 0 | None. All conflict resolutions are consistent and complete. |
| **INFO** | 2 | Clean merge topology with candidate follow-up commit `8c56beb8b4`; single dependency update in `uv.lock`. |

---

## 4. Invariant Preservation & Conflict Audit

### C01: `acp_adapter/session.py`
- **Specification**: `design.md` Decision D8 / Conflict Matrix C01.
- **Audit Findings**:
  - `max_iterations` resolution preserved at lines 398-406: reads `HERMES_MAX_ITERATIONS`, `config.acp.max_iterations`, `agent.max_iterations`/`max_turns`, with fallback 90.
  - Passed in kwargs to `AIAgent` (`"max_iterations": int(max_iter)`).
  - Adopted upstream `"cwd": cwd` in kwargs and `target_model=(model or default_model) or None` in `resolve_runtime_provider`.
- **Verdict**: PASS.

### C02: `apps/desktop/electron/main.ts` (SYNC-4, SYNC-5)
- **Specification**: `specs/upstream-sync-preservation/spec.md` SYNC-4, SYNC-5; `design.md` Decisions D3, D4.
- **Audit Findings**:
  - `executeWithNativeBearerSingleReplay` imported at L288 and invoked at L16062 within `requestWithOauthFallback`.
  - Force-refresh trigger passes `rejectedAccessToken: rejectedBearer` at L16067.
  - Latch check `ensureNativeAccessToken.isTerminalSignedOut?.(descriptor.baseUrl)` at L16041 throws 401 with `code: 'ERR_SIGNED_OUT'`, suppressing cascading unauthenticated network requests.
  - Terminal signed-out hosts tracked via `terminalSignedOutHosts` map (L8091), with `broadcastAuthTerminalStateChanged` notifying renderer windows (L8058).
  - `clearTerminalSignedOut(baseUrl)` invoked upon confirmed sign-in (L16149, L16176) and explicit logout (L16207).
  - Single global reauth modal latching preserved via `reauthModalLatch` at L16082-16110.
  - Upstream additions (`cloud-discovery`, `command-screenshot`, `backend-exit-recovery`) placed cleanly around auth coordinators without nesting retry loops.
- **Verdict**: PASS.

### C03: `apps/desktop/scripts/set-exe-identity.mjs`
- **Specification**: `design.md` Decision D10 / Conflict Matrix C03.
- **Audit Findings**:
  - `buildRceditOptions` (L129-182) strictly enforces fail-closed SemVer parsing (L148-152) and 16-bit unsigned integer boundary validation [0..65535] across major, minor, patch (L157-161).
  - 4-tuple PE build component calculated via `peBuildNumber = rawBuild % 65536` (L167).
  - Integrates upstream retry loop `RCEDIT_COMMIT_RETRY_DELAYS_MS = [500, 1000, 2000]` (L14, L72-84) to absorb transient AV/EDR file locks while failing permanently on spawn errors via `isPermanentRceditFailure` (L22-24).
- **Verdict**: PASS.

### C04 & C05: `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts` & `.test.ts` (SYNC-5)
- **Specification**: `specs/upstream-sync-preservation/spec.md` SYNC-5; `design.md` Decision D4.
- **Audit Findings**:
  - In `use-project-tree.ts`: `isTerminalSignedOut` guard preserved on `refreshRoot` (L78) and `loadChildren` (L442); `registerResumeSyncHandler` effect remains registered to resume deferred sync once upon sign-in.
  - Upstream `showIgnored` toggle (L19, L75), `setShowIgnored` (L90-99), and `showsIgnoredFiles` snapshotting guard (L36, L49, L62, L108) cleanly merged.
  - In `use-project-tree.test.ts`: Union of all 26 test cases. Preserved tests: "suppresses loadRoot directory reads when connection is in terminal signed-out state" (L562) and "resumes and coalesces deferred tree sync once upon confirmed sign-in" (L581). Upstream tests: "keeps expanded folders populated when the show-ignored preference flips" (L612) and "drops a child listing that was read before the preference flipped" (L677).
- **Verdict**: PASS.

### C06: `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx`
- **Specification**: `design.md` Conflict Matrix C06.
- **Audit Findings**:
  - Local test harness state setup (`$gatewayState.set('open')` in `beforeEach` L91, `$gatewayState.set('idle')` in `afterEach` L184) and `@/hermes` importOriginal mock (L160-168) preserved.
  - Upstream `confirmMock` (L35-37) and `@/i18n` importOriginal mock (L19-33) merged.
  - Retains test coverage for both guarded model confirmation dialog and "keeps the current model when the guarded switch is declined (#112458)" (L404).
- **Verdict**: PASS.

### C07: `plugins/platforms/buzz/adapter.py` (SYNC-6)
- **Specification**: `specs/upstream-sync-preservation/spec.md` SYNC-6; `design.md` Decision D5.
- **Audit Findings**:
  - Invariant constants strictly preserved:
    - `_DEFAULT_WS_OPEN_TIMEOUT = 30.0` (L144)
    - `_DEFAULT_WS_PING_INTERVAL = 30.0` (L145)
    - `_DEFAULT_WS_PING_TIMEOUT = 60.0` (L146)
    - `SUPPORTS_MESSAGE_EDITING = False` (L513)
  - Connection init uses preserved defaults at L546-560.
  - Upstream detached read task cleanup `_consume_ws_read_task` (L168-173) and `ConnectionClosed` handling in `_ws_discovery_loop` (L1142) cleanly integrated without mutating keepalive timing.
- **Verdict**: PASS.

### C08: `tests/hermes_cli/test_mcp_startup.py`
- **Specification**: `design.md` Conflict Matrix C08.
- **Audit Findings**:
  - `_install_retry_stubs` includes `status: str = "configured"` (L312) and maps `"name": "demo", "status": status` (L327).
  - Preserves local tests `test_has_configured_mcp_servers_disabled_returns_false` (L445) and `test_background_discovery_skips_when_all_servers_disabled` (L466).
  - Incorporates upstream `test_lazy_only_discovery_counts_as_usable_at_both_startup_sites` (L489).
- **Verdict**: PASS.

### C09: `tui_gateway/methods_prompt.py`
- **Specification**: `design.md` Decision D9 / Conflict Matrix C09.
- **Audit Findings**:
  - Local compute-host error cleanup preserved: releases running flag and clears inflight turn under `history_lock` on dispatch failure (L657-660), failing closed under turn isolation.
  - Error 4092 pre-existing in-process session check preserved (L101-102).
  - Upstream additions integrated: `_session_turn_admission(session)` with retiring error 5035 (L527-529, L975), `display_kind` propagation, and `_persist_submit_user_row` (L462).
- **Verdict**: PASS.

### C10: `uv.lock` (SYNC-8)
- **Specification**: `specs/upstream-sync-preservation/spec.md` SYNC-8; `design.md` Decision D7.
- **Audit Findings**:
  - Manifest `pyproject.toml` cleanly reconciled with upstream test marker addition (`real_post_swap_handoff`).
  - `uv.lock` has 1 clean insertion (`google-cloud-pubsub = false`).
  - `uv lock --check` verified clean (exit code 0, "Resolved 260 packages in 1ms").
- **Verdict**: PASS.

---

## 5. Auto-Merged Dependencies & Invariants Audit

1. **SYNC-3 (Host Auth Ownership & Token Persistence)**:
   - `hermes_cli/dashboard_auth/routes.py`: Identical to local baseline (`git diff` clean). Process-home session store binding and `/auth/native/logout` intact.
   - `hermes_cli/dashboard_auth/login_page.py`: Identical to local baseline (`git diff` clean).
   - `hermes_cli/web_server.py`: Startup 503 grace `Retry-After: 3` and persistent token `.dashboard_session_token` with 0o600 permissions intact.
   - **Status**: Intact.

2. **SYNC-4 (Desktop Native Bearer 401 Single-Replay)**:
   - `apps/desktop/electron/native-auth-decisions.ts`: Identical to local baseline (`git diff` clean). Single-replay decision logic and unexpired 401 force-refresh intact.
   - **Status**: Intact.

3. **SYNC-5 (Desktop Terminal-Auth Suspension & Reconnect Resilience)**:
   - `apps/desktop/src/store/profile.ts`: Disconnected cache retention, `isTerminalSignedOut()` suppression, and error absorption during reconnect intact.
   - **Status**: Intact.

4. **SYNC-6 (Buzz Keepalive & Non-Editing Delivery)**:
   - `plugins/platforms/buzz/adapter.py`: 30s/60s timeouts and `SUPPORTS_MESSAGE_EDITING = False` intact.
   - `gateway/platforms/base.py` & `gateway/run.py`: Non-editing platform delivery contract intact.
   - **Status**: Intact.

5. **SYNC-7 (Multi-Provider JWKS & Native Chooser)**:
   - `hermes_cli/dashboard_auth/routes.py` & `login_page.py`: Canonical chooser and PKCE validation intact.
   - **Status**: Intact.

---

## 6. Empirical Verification Evidence

All tests were executed in the clean integration worktree `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2` on exact candidate commit `8c56beb8b4596b4686799d1bfbb0a5456232e20a`.

### 1. Git Ancestry & Clean Worktree Preflight
```bash
git rev-parse HEAD
# Output: 8c56beb8b4596b4686799d1bfbb0a5456232e20a
git merge-base --is-ancestor 77c21ec557 HEAD && echo "Ancestry 77c21ec557: PASS"
# Output: Ancestry 77c21ec557: PASS
git merge-base --is-ancestor c661785f87 HEAD && echo "Ancestry c661785f87: PASS"
# Output: Ancestry c661785f87: PASS
git status --porcelain
# Output: (clean, exit 0)
```

### 2. Desktop Typecheck (3 tsconfigs)
```bash
cd apps/desktop && npm run typecheck
```
- **Command**: `tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit && tsc -p tsconfig.e2e.json --noEmit`
- **Exit Code**: `0`
- **Result**: 0 TypeScript compilation errors across all 3 project configurations.

### 3. Desktop Affected Vitest Suites (SYNC-4, SYNC-5)
```bash
cd apps/desktop && npx vitest run src/app/right-sidebar/files/use-project-tree.test.ts src/app/session/hooks/use-model-controls.test.tsx electron/native-auth-decisions.test.ts
```
- **Exit Code**: `0`
- **Duration**: 3.73s
- **Result**: **3 test files passed, 82/82 tests passed (100%)**.
  - `use-project-tree.test.ts`: 26 passed
  - `use-model-controls.test.tsx`: 25 passed
  - `native-auth-decisions.test.ts`: 31 passed

### 4. Buzz Platform WebSocket Pytest Suite (SYNC-6)
```bash
pytest tests/gateway/test_buzz_websocket.py
```
- **Exit Code**: `0`
- **Duration**: 41.21s
- **Result**: **26/26 tests passed (100%)**.

### 5. MCP Startup Pytest Suite (C08)
```bash
pytest tests/hermes_cli/test_mcp_startup.py
```
- **Exit Code**: `0`
- **Duration**: 1.38s
- **Result**: **19/19 tests passed (100%)**.

### 6. Dependency Lockfile Verification (SYNC-8)
```bash
uv lock --check
```
- **Exit Code**: `0`
- **Result**: "Resolved 260 packages in 1ms". Consistent with `pyproject.toml`.

---

## 7. Informational Notes (INFO)

- **INFO-1**: The candidate commit `8c56beb8b4596b4686799d1bfbb0a5456232e20a` is a single syntax-closure commit (`fix(test): add missing closing block in use-project-tree.test.ts`) immediately atop merge commit `d8e3f324ecb276ef4be181070a11ad8c6e153e86`. Both-parent ancestry (`77c21ec557` and `c661785f87`) is confirmed.
- **INFO-2**: `apps/desktop/scripts/set-exe-identity.mjs` successfully incorporates upstream AV/EDR retry timing (`RCEDIT_COMMIT_RETRY_DELAYS_MS`) while strictly maintaining fail-closed SemVer and 16-bit tuple validation.

---

## 8. Final Verdict

**VERDICT**: **APPROVED FOR DOWNSTREAM QA**

Candidate commit `8c56beb8b4596b4686799d1bfbb0a5456232e20a` satisfies all acceptance criteria of Milestone 3. All 10 content conflict files are correctly reconciled, all 5 core invariants (SYNC-3 through SYNC-7) remain fully preserved, and all focused verification test suites pass 100% with exit code 0. Unblocks downstream QA verification.
