# Independent P0 QA Audit & Exact Auth-Boundary Storm Verification Report (v2)

- **Change ID**: `stabilize-dashboard-auth-runtime-ownership`
- **Task ID**: `t_b4209adf`
- **Agent**: `Agent QA` (Process Compliance Guardian)
- **Candidate Task ID**: `t_f7ec2ae8`
- **Candidate Branch / Worktree**: `wt/t_f7ec2ae8` (`O:/workspaces/oss/hermes-agent/.worktrees/p0-auth-convergence-assembly-v2`)
- **Review Task ID**: `t_ed6f835e` (Verdict: PASS)
- **QA Verdict**: **PASS** (Candidate Accepted for Controlled Deployment; Zero Blocker, Zero Critical, Zero Warning)

---

## 1. Candidate Bundle & Change Manifest Verification

### 1.1 Git Base & Diff Fingerprints
- **Base HEAD SHA**: `28a121de2d5b5196965b445e077b5667d1dac7d9`
- **Tracked Diff SHA-256**: `d90250360e9536a01db5efae4dbce4b63de59e17a1aed7123084ab0cd0db2df4`
- **Status**: Clean 25-file manifest match. Every file's SHA-256 matches the upstream Reviewer manifest bit-for-bit with 100% exact fidelity.

### 1.2 File SHA-256 Checksums
- `apps/desktop/electron/main.ts`: `cbe441d668fc72ee6b3c0765954a1be753cae02ee19a26db7c6c06a86e926214`
- `apps/desktop/electron/native-auth-decisions.test.ts`: `f3a3d5e27a6c0bbfec2c32cf97b6cfd21f8a84897f26792ea7949514e8674996`
- `apps/desktop/electron/native-oauth.ts`: `3faee5038ec8b368735391218df85cb7974bc73a97ae89d14601bf19430c33d8`
- `apps/desktop/electron/native-token-refresh.test.ts`: `2f61a15324aa8a9d1858c4fe7fec966c8f936fdad30a9e22709230ea740ba731`
- `apps/desktop/electron/native-token-refresh.ts`: `083a21644bf42bc4cf204481023a886f78f8ddc643b006bb9f11651fe8365287`
- `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts`: `a4f7832ef6c413554b73b5bf5ffad227a69b2d867c4d360098dfbf1219b165fb`
- `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts`: `8dd415b367d3eec41103c812d4d812239d564cf14432612f00a38f36c53e051d`
- `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts`: `161244e8ec6aa4bc9599580bbf837a7b889bcab75953051cc7293e4d92ee2577`
- `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts`: `53a7b670db47e74aa7356c9a9d20c57c43336499898236bc7520e5401673cc35`
- `apps/desktop/src/app/session/hooks/use-hermes-config.test.ts`: `f2628469cb91ee4c0cff0da994b79193156cfd7fa281ba57fc10915f02c6fe90`
- `apps/desktop/src/app/session/hooks/use-hermes-config.ts`: `a906ca27d6f51c720516752763b65cb684c3ea83c5fa4e6a88b209a32c74d642`
- `apps/desktop/src/store/auth-terminal-state.test.ts`: `4eaeb94918e9c15d7e527b1e42f9e428dfeb6903f675662bb1e2aa0fc15b1e60`
- `apps/desktop/src/store/auth-terminal-state.ts`: `a09f874bc73c41ea4184c7faad67c7e5a1b32d203598d9cc9ffdf3709b1fdb5a`
- `apps/desktop/src/store/profile.test.ts`: `1409da781c7e937d5ef9b07106cf59139fec549ec23f0ae812e9649586fe0478`
- `apps/desktop/src/store/profile.ts`: `b6e9fa2fa74db1f47dd84f358ea82d3345f3d45cc4bca620fe1fc3fbdb1d9fa0`
- `hermes_cli/dashboard_session_resilience.py`: `bf0954beeb08552178ee9d49eb403759a2283da3e1104e9c7d41fdb7020084c6`
- `plugins/dashboard_auth/__init__.py`: `70395cb0131495c02604d5093557e1cbf40656a81fa39e6a00f28e2023531631`
- `plugins/dashboard_auth/self_hosted/__init__.py`: `fa03930e4394f48ff112ea11b81628d095bc3be61073860bb4dd5ba1cb1cf68c`
- `plugins/dashboard_auth/self_hosted/sessions.py`: `0154e17efc8fe3e04c5520935593c20092c4740e53a3c22fffa985be0b6aeb63`
- `tests/hermes_cli/test_dashboard_session_resilience.py`: `f8e3f94c965c71b14c33580579e0a0d4cbb8f4a25925e0bbfbaef08419e761c5`
- `tests/plugins/dashboard_auth/test_dashboard_auth_host_ownership.py`: `23d9faaf4a69be745dd50201d4bfe8aa7c6f05e0ae2b1e42f56ce48e24c2fc93`
- `tests/plugins/dashboard_auth/test_native_token_refresh.py`: `6f8d3844f6f8f533a6b5a371ca0ec79998858df34925e100f9a21287933f7c12`
- `tests/plugins/dashboard_auth/test_oidc_application_sessions.py`: `42a420ee9b5ee2ee003fc84e72aa79ba921e16fdf9cf9d40b490f2b38ef04652`
- `tests/plugins/dashboard_auth/test_oidc_provider.py`: `7f5e3e2cbeae11c8d76d4957f9fa68cfad0926521a09d6f30a91e01f03f3fb66`
- `tests/plugins/dashboard_auth/test_token_exchange_ipc.py`: `bdf129fbaf6e3d2ee1789c679e96f183707e77b63266f8ec47fe5e54d3cb62f4`

---

## 2. Canonical Test & Verification Results

### 2.1 Backend Canonical Tests
- **Command**: `HERMES_PYTHON="O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe" bash scripts/run_tests.sh tests/plugins/dashboard_auth/ tests/hermes_cli/test_dashboard_session_resilience.py`
- **Result**: `178 passed, 1 skipped` (Windows platform skip), `exit_code: 0`.

### 2.2 Desktop Vitest Suites
- **Command**: `npx vitest run electron/native-token-refresh.test.ts src/store/auth-terminal-state.test.ts src/store/profile.test.ts src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts src/app/right-sidebar/files/use-project-tree.test.ts src/app/session/hooks/use-hermes-config.test.ts electron/native-auth-decisions.test.ts`
- **Result**: `7 passed files (7), 133 passed tests (133), 0 failed`, `exit_code: 0`.

### 2.3 Electron TypeScript Typecheck
- **Command**: `npx tsc -p tsconfig.electron.json --noEmit`
- **Result**: Clean exit `0` (0 errors).

### 2.4 Desktop Renderer TypeScript Typecheck
- **Command**: `npx tsc -p . --noEmit`
- **Result**: Clean exit `0` (0 errors).

### 2.5 Strict OpenSpec Validation
- **Command**: `openspec validate stabilize-dashboard-auth-runtime-ownership --strict`
- **Result**: `Change 'stabilize-dashboard-auth-runtime-ownership' is valid`, `exit_code: 0`.

---

## 3. Independent Auth-Boundary & Terminal-State Probes

### 3.1 Backend Multi-Profile & Auth-Store Isolation Probes
- **Script**: `O:/workspaces/oss/hermes-agent/.worktrees/qa-p0-auth-boundary-v2/qa_backend_probes.py`
- **Probes Executed & Results**:
  1. `Probe B1: Host Process-Home Immutability Under Route Overrides`:
     - Verified that setting request-scoped profile overrides (`set_hermes_home_override`) and attempting `register_global_provider` or `ctx.register_dashboard_auth_provider` logs `rejected replacing host provider 'self-hosted' with request-scoped instance`.
     - Host session store DB path remains strictly bound to `host_home/dashboard-auth-sessions.db`.
     - Routed profile directory receives no leaked DB or credentials.
     - **Status**: PASS.
  2. `Probe B2: Named Profile Process Isolation`:
     - Verified that running with `HERMES_HOME=.../profiles/agent4070` binds the auth provider strictly to `.../profiles/agent4070/dashboard-auth-sessions.db`.
     - No default `~/.hermes/dashboard-auth-sessions.db` is touched or created.
     - **Status**: PASS.
  3. `Probe B3: Non-Destructive Existing DB Compatibility`:
     - Pre-populated SQLite DB with existing schema (`oidc_sessions`, `oidc_access_tokens`).
     - Issued session, verified token hash, refreshed token, and confirmed existing rows were preserved without table drops or schema loss.
     - **Status**: PASS.
  4. `Probe B4: Zero-Secret Audit Across Candidate Files`:
     - Scanned all 19 source files for private keys, tokens, session cookies, or plaintext credentials. Zero detected.
     - **Status**: PASS.

### 3.2 Desktop Terminal-State & Reconnection Probes
- **Script**: `O:/workspaces/oss/hermes-agent/.worktrees/qa-p0-auth-boundary-v2/qa_desktop_probe_runner.ts`
- **Probes Executed & Results**:
  1. `Probe D1: Base URL Normalization & State Isolation`:
     - Verified `normalizeBaseUrl` trims whitespace, trailing slashes, and lowercases hostname.
     - Terminal signed-out transitions isolate state per base URL; other URLs remain unaffected.
     - **Status**: PASS.
  2. `Probe D2: Terminal 401 Rejection & Network Call Suppression`:
     - Authoritative 401 transitions refresher to terminal signed-out state and clears local credentials.
     - Subsequent calls immediately return `null` without dispatching background network requests or retries.
     - **Status**: PASS.
  3. `Probe D3: Compare-and-Set Generation Protection vs Newer Login`:
     - When fresh credentials are established, a delayed/stale in-flight 401 bearing `rejectedBearer: 'old-access-token'` does NOT clear or mutate the fresh credentials.
     - Refresh network mock is not called.
     - **Status**: PASS.
  4. `Probe D4: Explicit Logout Semantics`:
     - Explicit logout marks terminal signed out (`reason: 'explicit_logout'`) and clears credentials.
     - Subsequent calls return `null` with 0 network calls.
     - **Status**: PASS.
  5. `Probe D5: One Coalesced Resume on Confirmed Sign-In`:
     - Transitioning signed-out state from `true` to `false` triggers registered resume sync handlers exactly once.
     - Subsequent redundant transitions do not duplicate resume execution.
     - **Status**: PASS.

---

## 4. OpenSpec Phase Artifact & Traceability Audit

- `proposal.md`: Fully aligns with runtime ownership and desktop reconnection requirements.
- `design.md`: Architecture diagrams and token lifecycle contracts accurately reflect candidate implementation.
- `specs/dashboard-auth/spec.md`: All normative scenarios satisfied and verified.
- `specs/desktop-reconnect-resilience/spec.md`: All normative scenarios satisfied and verified.
- `tasks.md`: All tasks marked complete (`[x]`), matching the reviewed diff.

---

## 5. Security & Secret Exposure Audit

- **Log & Error Scanning**: Checked test outputs and probe logs; all sensitive fields are redacted or hashed with SHA-256.
- **Database Migrations**: No destructive migrations, dropping of tables, or fallback searching.
- **Role Boundary Compliance**: QA agent performed read-only audits, test executions, and verification probes without modifying candidate code or docs under review.

---

## 6. QA Decision & Upstream PR Gate Status

- **Candidate Acceptance**: **ACCEPTED**
- **Upstream PR Gate**: **CLOSED** pending Commander-controlled deployment and extended staging observation.
- **Recommendation**: Upstream candidate `t_f7ec2ae8` satisfies all P0 quality, security, and process compliance standards. Ready for release orchestrator handoff.
