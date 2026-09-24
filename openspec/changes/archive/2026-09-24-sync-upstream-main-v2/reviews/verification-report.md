# Zero-Trust QA Verification Report — `sync-upstream-main-v2` (Milestone 4)

- **Candidate Commit**: `8c56beb8b4596b4686799d1bfbb0a5456232e20a`
- **Parent 1 (Local Base)**: `77c21ec55724b279dd89f7e87f58def91c72ee1d` (Option A: TS1294 & UnscopedSecretError on `origin/main`)
- **Parent 2 (Upstream Target)**: `c661785f872b5647fbac7c138d965180783bd9af` (`upstream/main`, ahead by 1,360 commits)
- **Merge Commit**: `d8e3f324ecb276ef4be181070a11ad8c6e153e86` (Candidate `8c56beb8b4` is single syntax fix directly atop merge)
- **Integration Worktree**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2`
- **Integration Branch**: `wt/review-sync-v2`
- **Auditor**: Agent QA (Process Compliance Guardian)
- **Date**: 2026-09-19
- **Scope**: Section 10 of `tasks.md`, `specs/upstream-sync-preservation/spec.md`, candidate identity, reviewer report audit, resolution ledger audit, regression test matrix, and production isolation verification.

---

## Executive Summary & Final Verdict

**VERDICT**: **PASSED / APPROVED FOR COMMANDER REVIEW**

Candidate commit `8c56beb8b4596b4686799d1bfbb0a5456232e20a` satisfies 100% of Milestone 4 acceptance criteria under zero-trust verification:
1. **Preflight & Identity**: Exact candidate commit verified (`8c56beb8b4596b4686799d1bfbb0a5456232e20a`). Worktree is completely clean (`git status --porcelain` exit code 0). Dual-parent ancestry confirmed for local baseline `77c21ec557` and upstream target `c661785f87`.
2. **Reviewer & Ledger Audit**: Independent Reviewer report `findings-reviewer-v1.md` and resolution ledger `resolution-ledger.md` audited. All 10 content conflict files (C01–C10) and all 5 core non-negotiable invariants (SYNC-3 through SYNC-7) are fully accounted for, verified intact, and supported by empirical test evidence.
3. **Regression Test Matrix**:
   - **Desktop Typecheck**: `npm run typecheck` across all 3 tsconfigs (root, electron, e2e) passed with exit code 0 (0 compilation errors).
   - **Desktop Affected Vitest Suites**: 82/82 tests passed across 3 suites (`use-project-tree.test.ts` 26 passed, `use-model-controls.test.tsx` 25 passed, `native-auth-decisions.test.ts` 31 passed) in 3.89s with exit code 0.
   - **Buzz Platform WebSocket Pytest**: 26/26 tests passed in 35.36s with exit code 0 (`test_buzz_websocket.py`).
   - **MCP Startup Pytest**: 19/19 tests passed in 0.99s with exit code 0 (`test_mcp_startup.py`).
   - **Dependency Lock Consistency**: `uv lock --check` resolved 260 packages in 1ms with exit code 0.
   - **Supplemental Backend Auth**: 50/50 tests passed in 7.01s with exit code 0 (`test_dashboard_auth_native_flow.py` and `test_dashboard_auth_gate.py`).
4. **Production Isolation**: Zero interference with live production services. Dashboard service on port 9119 (PID 71228) and Desktop application processes remain active and undisturbed. `origin/main` is untouched at commit `43ca20a5fd25ec415315bc93ca89370d4fd872b9` (zero commits pushed).
5. **Process Compliance**: Zero code fixes executed by QA. Role boundaries and MAS collaboration protocol strictly observed.

---

## 1. Preflight: Candidate Identity & Ancestry

All verification commands executed directly in integration worktree `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2`:

### 1.1 Commit Identity Check
```bash
$ cd O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2
$ git rev-parse HEAD
8c56beb8b4596b4686799d1bfbb0a5456232e20a
```
- **Result**: Matches exact candidate commit identity specified in task and Reviewer handoff.

### 1.2 Working Tree Cleanliness
```bash
$ git status --porcelain
(no output, exit code 0)
```
- **Result**: Working tree is 100% clean.

### 1.3 Dual-Parent Ancestry Verification
```bash
$ git merge-base --is-ancestor 77c21ec55724b279dd89f7e87f58def91c72ee1d HEAD && echo "ANCESTOR_BASE_OK"
ANCESTOR_BASE_OK
# Exit code: 0

$ git merge-base --is-ancestor c661785f872b5647fbac7c138d965180783bd9af HEAD && echo "ANCESTOR_UPSTREAM_OK"
ANCESTOR_UPSTREAM_OK
# Exit code: 0
```
- **Result**: Confirmed direct ancestry from both local baseline `77c21ec557` (Option A) and upstream target `c661785f87`.

### 1.4 Commit Graph & Lineage
```bash
$ git log -n 5 --oneline --graph
* 8c56beb8b4 fix(test): add missing closing block in use-project-tree.test.ts
*   d8e3f324ec Merge remote-tracking branch 'upstream/main' into sync/upstream-main-v2
|\  
| * c661785f87 test(cron): R3-S4 keep the external-status negative assertion relevant
| * d63876e820 refactor(cron): R3-S3 remove redundant satellite PID checks
| * ad1bf68a21 refactor(cron): R3-S1 share dispatch labels and missed-fire guidance
```
- **Result**: Clean merge topology. Merge commit `d8e3f324ec` bridges the branches; candidate commit `8c56beb8b4` adds the syntax closure for `use-project-tree.test.ts`.

---

## 2. OpenSpec Specification & Artifact Audit

### 2.1 OpenSpec Strict Validation
```bash
$ cd O:/workspaces/oss/hermes-agent
$ openspec validate sync-upstream-main-v2 --strict
Change 'sync-upstream-main-v2' is valid
# Exit code: 0
```
- **Result**: Strict OpenSpec schema and artifact validation passed with zero errors.

### 2.2 Phase Artifacts Traceability
- **`proposal.md`**: Outlines motivation, scope, and non-negotiable invariants for merging upstream `c661785f87` into `sync/upstream-main-v2`.
- **`design.md`**: Provides architectural decisions (D1–D10) and the complete 10-file Conflict Matrix (C01–C10).
- **`specs/upstream-sync-preservation/spec.md`**: Establishes formal behavior requirements for invariants SYNC-1 through SYNC-8.
- **`tasks.md`**: Structures 10 discrete task sections. Section 1 (Preflight), Section 9 (Reviewer), and Section 10 (QA) accurately reflect the milestone lifecycle.
- **`reviews/resolution-ledger.md`**: Reconciles each conflict path with explicit line numbers, resolution strategies, and verification test suites.
- **`reviews/findings-reviewer-v1.md`**: Milestone 3 independent code review report by Agent Reviewer with verdict `APPROVED FOR DOWNSTREAM QA`.

---

## 3. Conflict Matrix & Invariant Audit

All 10 content conflict files and 5 core invariants were audited against `findings-reviewer-v1.md`, `resolution-ledger.md`, and the actual source tree:

| Conflict ID | File Path | Invariant / Topic | Resolution Strategy | QA Audit Result |
|:---:|:---|:---|:---|:---:|
| **C01** | `acp_adapter/session.py` | Decision D8 | Preserved local `max_iterations` config resolution (fallback 90); adopted upstream `cwd` in kwargs and `target_model` in provider resolution. | **VERIFIED** |
| **C02** | `apps/desktop/electron/main.ts` | **SYNC-4**, **SYNC-5** | Preserved `executeWithNativeBearerSingleReplay`, `ensureNativeAccessToken`, `reauthModalLatch`, and terminal-auth suspension without cascading unauthenticated requests; integrated upstream discovery/screenshot/exit hooks. | **VERIFIED** |
| **C03** | `apps/desktop/scripts/set-exe-identity.mjs` | Decision D10 | Retained local fail-closed SemVer validation and 16-bit tuple validation in `buildRceditOptions()`; incorporated upstream AV/EDR retry loop (`RCEDIT_COMMIT_RETRY_DELAYS_MS`). | **VERIFIED** |
| **C04** | `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts` | **SYNC-5** | Unioned local SYNC-5 terminal suppression & resume sync tests with upstream show-ignored preference tests (26/26 tests). | **VERIFIED** |
| **C05** | `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts` | **SYNC-5** | Preserved local `isTerminalSignedOut` guards on `refreshRoot`/`loadChildren` & `registerResumeSyncHandler`; integrated upstream `showIgnored` toggle & snapshotting. | **VERIFIED** |
| **C06** | `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx` | Decision D4 | Preserved local gateway state setup (`$gatewayState.set('open')`) & `@/hermes` mock; integrated upstream `confirmMock` & `@/i18n` mocks (25/25 tests). | **VERIFIED** |
| **C07** | `plugins/platforms/buzz/adapter.py` | **SYNC-6** | Strictly preserved WAN keepalive timeouts (30s interval / 60s timeout) and `SUPPORTS_MESSAGE_EDITING = False`; integrated upstream `_consume_ws_read_task` and `ConnectionClosed` handling. | **VERIFIED** |
| **C08** | `tests/hermes_cli/test_mcp_startup.py` | Invariant C08 | Updated `_install_retry_stubs` with `status: str = "configured"`; retained upstream lazy-only discovery test; preserved local disabled-server tests (19/19 tests). | **VERIFIED** |
| **C09** | `tui_gateway/methods_prompt.py` | Decision D9 | Preserved compute-host error cleanup (releasing running flag, clearing inflight turn under history lock) and 4092 pre-existing session error; integrated upstream `display_kind` and 5035 retiring turn admission. | **VERIFIED** |
| **C10** | `uv.lock` | **SYNC-8** | Cleanly regenerated and reconciled; `uv lock --check` verified clean with 0 differences. | **VERIFIED** |

### Invariant Status Summary
- **SYNC-3 (Process-Home Auth Ownership & Token Persistence)**: Intact. `hermes_cli/dashboard_auth/routes.py`, `login_page.py`, and `web_server.py` diff clean against base. 503 startup grace `Retry-After: 3` and `.dashboard_session_token` (0o600) intact.
- **SYNC-4 (Desktop Native Bearer 401 Recovery)**: Intact. Single-replay execution `executeWithNativeBearerSingleReplay` and force-refresh on unexpired token preserved. 31/31 Vitest tests pass.
- **SYNC-5 (Terminal-Auth Suspension & Reconnect Resilience)**: Intact. Disconnected cache retention, `isTerminalSignedOut` suppression, and resume sync handler preserved. 26/26 Vitest tests pass.
- **SYNC-6 (Buzz Keepalive Tuning & Non-Editing Delivery)**: Intact. 30s/60s timeouts and `SUPPORTS_MESSAGE_EDITING = False` preserved. 26/26 pytest tests pass.
- **SYNC-7 (Multi-Provider JWKS Classification & Native Chooser)**: Intact. Foreign `kid` classified as unverifiable, PKCE challenge validation, and brokerable chooser preserved. 50/50 backend auth tests pass.

---

## 4. Empirical Zero-Trust Regression Test Matrix

### 4.1 Dependency Lockfile Consistency (`uv lock --check`)
- **Directory**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2`
- **Command**: `uv lock --check`
- **Exit Code**: `0`
- **Raw Transcript**:
  ```text
  Resolved 260 packages in 1ms
  ```
- **Evaluation**: Lockfile is fully synchronized and reproducible with `pyproject.toml`.

### 4.2 Desktop TypeScript Typecheck (`npm run typecheck`)
- **Directory**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2/apps/desktop`
- **Command**: `npm run typecheck` (`tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit && tsc -p tsconfig.e2e.json --noEmit`)
- **Exit Code**: `0`
- **Raw Transcript**:
  ```text
  > hermes@0.17.6 typecheck
  > tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit && tsc -p tsconfig.e2e.json --noEmit
  ```
- **Evaluation**: Zero TypeScript compilation errors across all 3 project configurations (Renderer, Electron Main/Preload, E2E).

### 4.3 Affected Desktop Vitest Suites (82/82 Passed)
- **Directory**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2/apps/desktop`
- **Command**: `npx vitest run src/app/right-sidebar/files/use-project-tree.test.ts src/app/session/hooks/use-model-controls.test.tsx electron/native-auth-decisions.test.ts --reporter=verbose`
- **Exit Code**: `0`
- **Duration**: 3.89s
- **Suite Breakdown**:
  - `electron/native-auth-decisions.test.ts`: **31 passed** (0 failed, 0 skipped)
  - `src/app/right-sidebar/files/use-project-tree.test.ts`: **26 passed** (0 failed, 0 skipped)
  - `src/app/session/hooks/use-model-controls.test.tsx`: **25 passed** (0 failed, 0 skipped)
- **Total**: **82 passed out of 82 tests (100% pass rate)**.
- **Evaluation**: All native auth recovery, terminal suspension, resume sync, and model control invariants pass under test execution.

### 4.4 Buzz Platform WebSocket Pytest Suite (26/26 Passed)
- **Directory**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2`
- **Command**: `pytest tests/gateway/test_buzz_websocket.py -v`
- **Exit Code**: `0`
- **Duration**: 35.36s
- **Raw Transcript**:
  ```text
  ============================= test session starts =============================
  platform win32 -- Python 3.11.14, pytest-9.1.1, pluggy-1.6.0 -- O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe
  rootdir: O:\workspaces\oss\hermes-agent\.worktrees\review-sync-v2
  configfile: pyproject.toml
  plugins: anyio-4.12.1, asyncio-1.3.0
  asyncio: mode=Mode.STRICT, debug=False
  collected 26 items

  tests/gateway/test_buzz_websocket.py::test_schnorr_sign_matches_official_bip340_vector_zero PASSED [  3%]
  tests/gateway/test_buzz_websocket.py::test_decode_private_key_rejects_bad_input PASSED [  7%]
  tests/gateway/test_buzz_websocket.py::test_build_auth_event_shape_and_owner_tag PASSED [ 11%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_reconnects_when_read_goes_silent PASSED [ 15%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_reconnects_when_discovery_send_sees_closed_socket PASSED [ 19%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_backs_off_and_publishes_retrying_on_clean_relay_close PASSED [ 23%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_idle_silent_channel_keeps_connection_alive_when_ping_succeeds PASSED [ 26%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_reconnects_when_ping_fails_or_times_out[ping_behavior0] PASSED [ 30%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_reconnects_when_ping_fails_or_times_out[ping_behavior1] PASSED [ 34%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_reconnects_when_ping_fails_or_times_out[timeout] PASSED [ 38%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_dispatches_frames_and_closes_cleanly PASSED [ 42%]
  tests/gateway/test_buzz_websocket.py::test_websocket_auth_raises_on_rejection PASSED [ 46%]
  tests/gateway/test_buzz_websocket.py::test_websocket_auth_uses_credentials_owner_tag PASSED [ 50%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_drops_restricted_channel_without_reconnect PASSED [ 53%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_reconnects_on_non_restricted_closed PASSED [ 57%]
  tests/gateway/test_buzz_websocket.py::test_restricted_channels_skipped_during_subscribe PASSED [ 61%]
  tests/gateway/test_buzz_websocket.py::test_new_subscription_without_high_water_mark_has_no_since_floor PASSED [ 65%]
  tests/gateway/test_buzz_websocket.py::test_seeded_subscription_resumes_from_high_water_mark PASSED [ 69%]
  tests/gateway/test_buzz_websocket.py::test_closed_membership_phrases_prune_without_reconnect[restricted: not a channel member] PASSED [ 73%]
  tests/gateway/test_buzz_websocket.py::test_closed_membership_phrases_prune_without_reconnect[not a channel member] PASSED [ 76%]
  tests/gateway/test_buzz_websocket.py::test_closed_membership_phrases_prune_without_reconnect[auth-required: subscription needs auth] PASSED [ 80%]
  tests/gateway/test_buzz_websocket.py::test_restricted_channel_not_readopted_by_discovery PASSED [ 84%]
  tests/gateway/test_buzz_websocket.py::test_ws_discovery_loop_subscribes_newly_discovered_conversation PASSED [ 88%]
  tests/gateway/test_buzz_websocket.py::test_ws_discovery_task_cancelled_when_connection_exits PASSED [ 92%]
  tests/gateway/test_buzz_websocket.py::test_websocket_keepalive_defaults_and_configuration PASSED [ 96%]
  tests/gateway/test_buzz_websocket.py::test_websocket_loop_passes_configured_keepalive_to_connect PASSED [100%]

  ============================= 26 passed in 35.36s =============================
  ```
- **Evaluation**: 100% of Buzz WebSocket tests passed cleanly with 0 failures and 0 flakes.

### 4.5 MCP Background Startup Pytest Suite (19/19 Passed)
- **Directory**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2`
- **Command**: `pytest tests/hermes_cli/test_mcp_startup.py -v`
- **Exit Code**: `0`
- **Duration**: 0.99s
- **Raw Transcript**:
  ```text
  ============================= test session starts =============================
  platform win32 -- Python 3.11.14, pytest-9.1.1, pluggy-1.6.0 -- O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe
  rootdir: O:\workspaces\oss\hermes-agent\.worktrees\review-sync-v2
  configfile: pyproject.toml
  plugins: anyio-4.12.1, asyncio-1.3.0
  asyncio: mode=Mode.STRICT, debug=False
  collected 19 items

  tests/hermes_cli/test_mcp_startup.py::test_prepare_agent_startup_backgrounds_blocking_mcp_for_chat PASSED [  5%]
  tests/hermes_cli/test_mcp_startup.py::test_prepare_agent_startup_skips_discovery_when_chat_resolves_to_tui PASSED [ 10%]
  tests/hermes_cli/test_mcp_startup.py::test_prepare_agent_startup_keeps_discovery_for_non_chat_commands PASSED [ 15%]
  tests/hermes_cli/test_mcp_startup.py::test_background_mcp_discovery_suppresses_interactive_oauth PASSED [ 21%]
  tests/hermes_cli/test_mcp_startup.py::test_background_mcp_discovery_propagates_profile_secret_scope PASSED [ 26%]
  tests/hermes_cli/test_mcp_startup.py::test_portable_only_mcp_configuration_opens_startup_gate PASSED [ 31%]
  tests/hermes_cli/test_mcp_startup.py::test_set_mcp_server_filter_normalizes[None-None] PASSED [ 36%]
  tests/hermes_cli/test_mcp_startup.py::test_set_mcp_server_filter_normalizes[-None] PASSED [ 42%]
  tests/hermes_cli/test_mcp_startup.py::test_set_mcp_server_filter_normalizes[all-None] PASSED [ 47%]
  tests/hermes_cli/test_mcp_startup.py::test_set_mcp_server_filter_normalizes[toolsets3-None] PASSED [ 52%]
  tests/hermes_cli/test_mcp_startup.py::test_set_mcp_server_filter_normalizes[terminal,web-expected4] PASSED [ 57%]
  tests/hermes_cli/test_mcp_startup.py::test_set_mcp_server_filter_normalizes[toolsets5-expected5] PASSED [ 63%]
  tests/hermes_cli/test_mcp_startup.py::test_discover_mcp_tools_spawns_only_allowed_servers PASSED [ 68%]
  tests/hermes_cli/test_mcp_startup.py::test_background_discovery_honors_server_filter PASSED [ 73%]
  tests/hermes_cli/test_mcp_startup.py::test_prepare_agent_startup_installs_server_filter PASSED [ 78%]
  tests/hermes_cli/test_mcp_startup.py::test_has_configured_mcp_servers_disabled_returns_false PASSED [ 84%]
  tests/hermes_cli/test_mcp_startup.py::test_background_discovery_skips_when_all_servers_disabled PASSED [ 89%]
  tests/hermes_cli/test_mcp_startup.py::test_lazy_only_discovery_counts_as_usable_at_both_startup_sites[lazy-False] PASSED [ 94%]
  tests/hermes_cli/test_mcp_startup.py::test_lazy_only_discovery_counts_as_usable_at_both_startup_sites[configured-True] PASSED [100%]

  ============================= 19 passed in 0.99s ==============================
  ```
- **Evaluation**: 19/19 MCP startup tests passed, including both local disabled-server tests and upstream lazy-only discovery verification.

### 4.6 Supplemental Invariant Coverage: Backend Auth Suites (50/50 Passed)
- **Directory**: `O:/workspaces/oss/hermes-agent/.worktrees/review-sync-v2`
- **Command**: `pytest tests/hermes_cli/test_dashboard_auth_native_flow.py tests/hermes_cli/test_dashboard_auth_gate.py -q`
- **Exit Code**: `0`
- **Duration**: 7.01s
- **Result**: `50 passed, 8 warnings in 7.01s` (warnings are standard upstream Starlette cookies deprecation and Python 3.13 audioop warnings; zero test errors).
- **Evaluation**: Covers SYNC-3 and SYNC-7 native PKCE authorization, provider chooser, and process-home binding.

---

## 5. Live Production Services & Environment Protection

Zero-trust audit verified that live production services, repositories, and databases were strictly protected:

1. **`origin/main` Ref Integrity**:
   - `git rev-parse origin/main`: `43ca20a5fd25ec415315bc93ca89370d4fd872b9`
   - Local `main`: `77c21ec55724b279dd89f7e87f58def91c72ee1d` (ahead by 1 commit containing reviewed runtime fixes Option A)
   - Zero commits or pushes executed to `origin/main`.
2. **Live Dashboard Service (Port 9119)**:
   - `netstat -ano | grep 9119` confirms `TCP 0.0.0.0:9119 LISTENING PID 71228` actively running with established connections (PID 27888).
   - Zero service restarts or interruptions caused by test suites.
3. **Live Desktop Application & ACP Processes**:
   - `tasklist | grep -i hermes` confirms Desktop runtime (`Hermes.exe` PIDs 33916, 53668, 47444, 9192, 12604) and ACP sidecars (`hermes-acp.exe` PIDs 21532, 58064) running continuously and undisturbed.
4. **Database & File System Isolation**:
   - Tests ran entirely in isolated integration worktree `.worktrees/review-sync-v2` using junctions for `node_modules` and shared virtualenv `.venv`. Zero writes to shared production databases or live config paths.

---

## 6. Process Compliance Findings

- **CRITICAL Findings**: `0` (None. All phase gates and invariants verified).
- **WARNING Findings**: `0` (None. All conflict resolutions are complete, verified, and passing).
- **INFO Findings**: `2`
  - **INFO-1**: The candidate commit `8c56beb8b4` is a clean follow-up commit to merge commit `d8e3f324ec` that closes the syntax block in `use-project-tree.test.ts`. Full both-parent ancestry is preserved.
  - **INFO-2**: Reviewer report `findings-reviewer-v1.md` and resolution ledger `resolution-ledger.md` are aligned with 100% agreement on all 10 conflict files and invariants SYNC-3 through SYNC-7.

---

## 7. Recommendation for Commander Sign-Off

Agent QA confirms candidate commit `8c56beb8b4596b4686799d1bfbb0a5456232e20a` passes all Milestone 4 quality and compliance gates.

The candidate is **ACCEPTED and APPROVED** for Commander review.
Once Commander authorizes Milestone 5, Leader may proceed with fast-forwarding or merging candidate `8c56beb8b4596b4686799d1bfbb0a5456232e20a` into `origin/main`.