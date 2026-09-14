# Independent Review Report: P0 Auth Runtime Ownership Convergence (`t_ed6f835e`)

## Executive Summary
- **Verdict**: **APPROVED** (Downstream Independent Verification Complete; QA Gate Released)
- **Change Under Review**: OpenSpec change `stabilize-dashboard-auth-runtime-ownership`
- **Candidate Location**: Canonical uncommitted integration worktree `O:/workspaces/oss/hermes-agent/.worktrees/p0-auth-convergence-assembly-v2`
- **Base Commit**: `28a121de2d5b5196965b445e077b5667d1dac7d9` (verified clean baseline)
- **Assembly Origin**: Mechanically assembled by `t_f7ec2ae8` from Backend (`t_d8792582`) and Desktop (`t_6814db97`) implementation handoffs.
- **Reviewer Working Directory**: `O:/workspaces/oss/hermes-agent/.worktrees/review-p0-auth-convergence-v2` (strictly read-only review; zero product bytes modified).

---

## Candidate Integrity & Cryptographic Receipts

- **Base HEAD SHA**: `28a121de2d5b5196965b445e077b5667d1dac7d9` (verified identical across all worktrees)
- **Source Receipts**:
  - Backend Full Bundle: `82108f22972831a0081a11d14fd9a7729ebb4c01a5e691c6ebb3ff8de4fe3e4e` (matches `t_d8792582` / `t_f7ec2ae8`)
  - Desktop Full Bundle: `1fff0dbdbb9a684aa68469f65cd1ba8f324401dd364b02a0ba87b5a1510eeeaf` (matches `t_6814db97` / `t_f7ec2ae8`)
- **Integration Diff Integrity**:
  - Tracked Diff SHA-256 (`git diff --binary HEAD | sha256sum`): `d90250360e9536a01db5efae4dbce4b63de59e17a1aed7123084ab0cd0db2df4`
  - Tracked Files Count: 16 modified files
  - Untracked Files Count: 9 untracked files (2 store/test files, 6 OpenSpec change files, 1 backend test file)
  - Total Candidate Files: 25 files
- **Every Candidate File SHA-256 Verified**:
  - `apps/desktop/electron/main.ts`: `65c58bca076c9267c220557b207800097a695a3c394befcc4a962a54e74f61a6`
  - `apps/desktop/electron/native-token-refresh.test.ts`: `a6fc26818fbe03f2d2549610ed82a69497f6b3af4f77067ef9c58875bae926dc`
  - `apps/desktop/electron/native-token-refresh.ts`: `f7a1be12acb9e32f3b4b18b7b0305a99e0a1b54d579c9054aa50c14bed4f812a`
  - `apps/desktop/electron/preload.ts`: `d468858bc5cd6ec6318bd5f995f10358b612a455a939dfc5609c5356e767a82d`
  - `apps/desktop/src/api/client.ts`: `8fe2548e297eab5c02e3425c656df1d742d9b086d9a831bbb204e3653a9b5776`
  - `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts`: `00d21ddeb0432f74d76767f368a6db33e7637459d10fb6957172802046df3082`
  - `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts`: `9b187c8f40afb8c3d9dd7392d837133ed91f15c703868410088a96aab85b0e18`
  - `apps/desktop/src/app/right-sidebar/files/use-project-tree.test.ts`: `0c7530e734c954f947496c7865efe841509a9de191d42899553fa80cbc0910c4`
  - `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts`: `936a8ed90a2b210b3f766a4fe0be348974b5c5c50d99296e3245a46e8f4d976f`
  - `apps/desktop/src/app/session/hooks/use-hermes-config.ts`: `5f6fea778c8df52a7f7b0d384d61f7179efceda8820520045ae9850ac17c6bda`
  - `apps/desktop/src/global.d.ts`: `111f51d53a2cf10d39d4117ffc167da7a8f24713d7a97b68207418a3cc67a868`
  - `apps/desktop/src/store/auth-terminal-state.test.ts`: `5d581ed322b579109db098b7ea509249b9c7a2ed24e0ba9de26f09ea5d466a67`
  - `apps/desktop/src/store/auth-terminal-state.ts`: `0a507ff8830488a1aca0328eaeda6712e29ece5d725dff390ef794371e1086e9`
  - `apps/desktop/src/store/profile.test.ts`: `ed5ed52045329bce7f4fb7455bc39edddace575e02eed4c2ec75f00932b5458d`
  - `apps/desktop/src/store/profile.ts`: `c16bcf576a34952653a80c8d9d41d7ec4cc36611f457e598a381dbcc9215ebea`
  - `hermes_cli/dashboard_auth/registry.py`: `bcec03cbd40618006286f4f2bfed0d101995d7a1ab9556e69f5738cc30c89fb0`
  - `hermes_cli/plugins.py`: `3906d6e86f2ba9db75b4d08e26c5b2412505c0f384bc86231cd03795fe4f3357`
  - `openspec/changes/stabilize-dashboard-auth-runtime-ownership/.openspec.yaml`: `99f4cb4cc789c6d4e665b552c7fc401530b10f538c6cf87d334842e868590303`
  - `openspec/changes/stabilize-dashboard-auth-runtime-ownership/design.md`: `733c9cb99134f2ab881664ea8c33b19f02900bafa5efdb72e86b13d47dc2746e`
  - `openspec/changes/stabilize-dashboard-auth-runtime-ownership/proposal.md`: `ce1d9aae4660909aa7f828ba3f23d3d9c0e5d4dbde06973debcc1b1407a9df60`
  - `openspec/changes/stabilize-dashboard-auth-runtime-ownership/specs/dashboard-auth/spec.md`: `b7ec8d7cfe8b711334c9bc4ed95efbec2d9f9412142a0820e14df4d708aee2cb`
  - `openspec/changes/stabilize-dashboard-auth-runtime-ownership/specs/desktop-reconnect-resilience/spec.md`: `6f60f0fde46feb5a8a215ef083587c2a47bd3b56f596010ce0137e46caf27884`
  - `openspec/changes/stabilize-dashboard-auth-runtime-ownership/tasks.md`: `540f4c0ba546aa8011bce8539519061d76143085d648a6a8fb5d973c6f09abad`
  - `plugins/dashboard_auth/self_hosted/__init__.py`: `648146b72a1a1c26de1502c5876c06c135b8be55bc37b5c965e54eb9075903f7`
  - `tests/plugins/dashboard_auth/test_dashboard_auth_host_ownership.py`: `d12da88b7362c5ca9eabf78bf42238c92ee7df5edf53f31e4a6c50409d468600`

---

## Independent Test & Verification Matrix

All commands were independently executed by Agent Reviewer directly in the integration worktree:

1. **Combined Backend Auth & Resilience Suites**:
   - Command: `HERMES_PYTHON="O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe" bash scripts/run_tests.sh tests/plugins/dashboard_auth/ tests/hermes_cli/test_dashboard_session_resilience.py`
   - Output: `Summary: 8 files, 178 tests passed, 0 failed, 1 skipped in 15.4s` (1 Linux-only skip expected on Windows; 0 failures).
2. **Desktop Vitest Suites (All 7 affected test files)**:
   - Command: `npx vitest run electron/native-token-refresh.test.ts src/store/auth-terminal-state.test.ts src/store/profile.test.ts src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts src/app/right-sidebar/files/use-project-tree.test.ts src/app/session/hooks/use-hermes-config.test.ts electron/native-auth-decisions.test.ts`
   - Output: `7 passed (7), 133 passed (133)` in 6.15s.
3. **Electron TypeScript Typecheck**:
   - Command: `npx tsc -p tsconfig.electron.json --noEmit`
   - Output: `exit 0` (clean, 0 type errors).
4. **Renderer TypeScript Typecheck**:
   - Command: `npx tsc -p . --noEmit`
   - Output: `exit 0` (clean, 0 type errors).
5. **OpenSpec Strict Validation**:
   - Command: `openspec validate stabilize-dashboard-auth-runtime-ownership --strict`
   - Output: `Change 'stabilize-dashboard-auth-runtime-ownership' is valid` (strict validation passed).

---

## Adversarial Correctness & Security Probes

Independent purpose-built adversarial probe executed via `run_adversarial_checks.py`:

1. **Adversarial Check 1: Provider Host Process-Home Ownership Under Route Override**:
   - Proved that when a host dashboard registers its auth provider under host home, any subsequent request-scoped routed profile override (simulating multi-agent routed traffic) attempting to replace the provider via `register_global_provider` or `PluginContext.register_dashboard_auth_provider` is strictly rejected with a warning log.
   - Result: **PASS** (`Host process-home ownership held immutable across request-scoped route overrides`).
2. **Adversarial Check 2: Named Profile Isolation and No Root Fallback**:
   - Proved that launching a process explicitly with a named profile (`HERMES_HOME=.../profiles/agent4070`) binds the session DB to that named profile directory without creating or polluting any global root directory.
   - Result: **PASS** (`Named profile process remains isolated to its own process home`).
3. **Adversarial Check 3: Authoritative Terminal Rejection Scoping & Network Suppression**:
   - Verified that terminal state is isolated per normalized base URL (`apps/desktop/src/store/auth-terminal-state.ts`).
   - Verified that `client.ts:31-38`, `profile.ts:133`, `use-profile-rail-refresh-on-active.ts:40-42`, `use-hermes-config.ts:81`, and `use-project-tree.ts:197,388,432,517` immediately suppress IPC calls, network fetches, recurring retry timers, and error toasts when terminal signed out.
   - Result: **PASS** (`Suppression prevents loop cascades and uncaught rejections`).
4. **Adversarial Check 4: Stale 401 CAS Protection vs Explicit Logout**:
   - Verified compare-and-set semantics in `apps/desktop/electron/native-token-refresh.ts:138-164`: in-flight rejections check credential generation before clearing, ensuring a stale in-flight 401 cannot overwrite credentials established by a newer login.
   - Verified explicit logout (`apps/desktop/electron/main.ts:983-1002`) invokes `/auth/native/logout` revocation on the backend and clears local tokens.
   - Result: **PASS** (`Race conditions mitigated; explicit logout semantics preserved`).
5. **Adversarial Check 5: Confirmed Sign-In Resume & Coalescing**:
   - Verified `registerResumeSyncHandler` in `auth-terminal-state.ts:60-72`. When sign-in is confirmed (`setTerminalSignedOut(url, false)`), registered sync handlers for that URL are triggered exactly once, and background operations (such as project tree reads) coalesce cleanly.
   - Result: **PASS** (`One-time clean resume observed`).
6. **Adversarial Check 6: Secret & Credential Leak Audit**:
   - Scanned all 25 candidate files and git diff for private keys, raw JWT tokens, API keys, passwords, and connection strings.
   - Result: **PASS** (`Zero sensitive credentials or tokens detected`).

---

## Findings & Severity Classification

- **CRITICAL**: 0 findings.
- **WARNING**: 1 finding (Non-blocking operational boundary):
  - `W-1`: Production stability and long-running runtime convergence cannot be proven in a static worktree; final validation remains contingent on Commander-controlled deployment and live observation of Desktop and Gateway telemetry.
- **INFO**: 1 finding:
  - `I-1`: Governance rule documents (`code-review-mandate/spec.md`, `lessons-learned.md`) were confirmed absent in the target repository root; review governance proceeded under canonical repository `AGENTS.md` and loaded SDLC review skills.

---

## Conclusion & Gate Routing
Every required acceptance criterion, technical constraint, and adversarial check in `t_ed6f835e` has been empirically verified with reproducible evidence.
- **Reviewer Action**: Calling `kanban_complete` on `t_ed6f835e`.
- **Downstream QA Release**: Task `t_b4209adf` is unblocked and authorized to proceed with independent QA compliance evaluation.
