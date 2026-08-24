# Handoff Report: Hermes Desktop & Gateway Process Isolation and Auth Gate Stability Fixes

**Document Version**: 1.0.0  
**Date**: 2026-08-24  
**Author**: Hermes Agent / Implementer Profile  
**Review Target**: Independent Agent / Reviewer Review Loop  
**Repository Target**: `O:\workspaces\oss\hermes-agent`  
**Applicable Governance / Contribution Guide**: `oss/hermes-agent/AGENTS.md` & `oss/hermes-agent/CONTRIBUTING.md` (Rubric, Invariants, Seams, Cross-Platform Rules, Security Boundaries)

---

## 1. Executive Summary

Following recent updates from upstream `NousResearch/hermes-agent` (commits between 2026-08-20 and 2026-08-24), a cascading set of regressions occurred on Windows environments:
1. **Gateway Process Termination**: Hermes Desktop's orphan process reaper identified standalone `hermes gateway` background services as orphaned backends and issued `taskkill /T /F` on them.
2. **IPC Scope Crash**: Renderer-to-Main IPC calls threw `ReferenceError: req is not defined` inside `main.ts:handleHermesApiRequest`.
3. **Loopback WebSocket Auth Rejection**: Configuring `dashboard.public_url` in `config.yaml` engaged the strict `auth_required = True` gate, causing the backend `/api/ws` endpoint to reject `?token=` query parameters on loopback `127.0.0.1` ephemeral sockets, trapping Desktop in a boot flapping loop.
4. **Model Catalog REST 401 Rejection**: `/api/model/options` was omitted from `PUBLIC_API_PATHS`, preventing Desktop from listing or selecting models.
5. **Desktop Internal REST 401 Rejection**: `gated_auth_middleware` rejected internal Electron REST requests (`/api/skills`, `/api/sessions`, `/api/config`) because Desktop authenticates via `X-Hermes-Session-Token` or Bearer tokens rather than browser session cookies.

All 5 root causes have been diagnosed, resolved with targeted surgical fixes, verified via live test suites (Python Pytest + Node Vitest + Starlette TestClient + Electron Vite Build), and staged in git for peer review.

---

## 2. Compliance with Hermes Contribution Rubric (`AGENTS.md`)

| Rubric Directive | Implementation Alignment | Evidence / Validation |
| :--- | :--- | :--- |
| **Fix real bugs, well** | Fixed the full class of loopback token auth and process matching bugs, not just point sites. | 164 web server tests pass, 30 auth gate tests pass, 134 desktop tests pass. |
| **Surgical changes** | Touched only necessary lines in `backend-ownership.ts`, `main.ts`, `middleware.py`, `public_paths.py`, and `web_server.py`. | Clean `git diff --cached` with zero unrelated reformatting. |
| **Narrow Core Waist** | No new core tools added; no modifications to LLM prompt cache prefixes or system prompts. | Zero impact on context caching or token overhead. |
| **Fail-Closed Security Preserved** | Non-loopback external exposure remains strictly gated behind OAuth/cookies; only verified loopback (`127.0.0.1`/`localhost`) presenting exact HMAC-verified `_SESSION_TOKEN` is permitted. | External internet requests still fail closed with 401 without cookie. |
| **E2E & Live Verification** | Real process execution, real HTTP/WS probes over loopback, real Vite packaging build. | Physical outputs captured directly from bash execution. |

---

## 3. Staged Files & Detailed Diff Breakdown

### A. `apps/desktop/electron/backend-ownership.ts` & `backend-ownership.test.ts`
* **Issue**: Regex `backendCommandMatches` matched commands containing `hermes_cli.main` and `serve|dashboard`, which could match CLI invocations or reused PIDs.
* **Fix**: Added explicit Negative Guard rejecting any command containing `gateway`, `cron`, `daemon`, `tools`, `doctor`, `acp`, `status`, or `setup`.
* **Tests**: Added negative assertions in `backend-ownership.test.ts` verifying that `gateway run`, `gateway start`, `cron run` return `false`.

### B. `apps/desktop/electron/main.ts`
* **Issue**: IPC handler `handleHermesApiRequest` used `req` in closure scopes where only `request` was declared, and lacked imports for `pathWithProfileScope` and `translateSelfProfileQuery`.
* **Fix**: Restored clean parameter scoping and missing imports.
* **Tests**: Verified with `npm run typecheck` (0 errors) and Vitest suite.

### C. `hermes_cli/web_server.py`
* **Issue**: In `_ws_auth_reason()`, when `auth_required = True`, `/api/ws` unconditionally rejected `?token=`, breaking Desktop's readiness probe `ws://127.0.0.1:<port>/api/ws?token=...`.
* **Fix**: Added loopback check: if client is in `_LOOPBACK_HOST_VALUES` and presents valid `_SESSION_TOKEN` via `?token=`, return accepted (`None, "token"`).
* **Tests**: Verified with `pytest tests/hermes_cli/test_web_server.py` (164 passed).

### D. `hermes_cli/dashboard_auth/public_paths.py`
* **Issue**: `/api/model/options` was missing from `PUBLIC_API_PATHS`, causing Desktop's model picker REST fallback to return 401.
* **Fix**: Appended `"/api/model/options"` to `PUBLIC_API_PATHS`.
* **Tests**: Verified retrieval across 11 providers and 106 models.

### E. `hermes_cli/dashboard_auth/middleware.py`
* **Issue**: `gated_auth_middleware` only checked for browser session cookies, rejecting internal Electron REST calls with `401 Unauthorized (no_cookie)` when `dashboard.public_url` engaged the gate.
* **Fix**: Permitted loopback clients presenting `_SESSION_TOKEN` via `X-Hermes-Session-Token` or `Authorization: Bearer` to authenticate with a synthetic `Session` instance.
* **Tests**: Verified `/api/config`, `/api/skills` (402 skills), `/api/sessions` (8 sessions) return HTTP 200 OK.

---

## 4. Verification Evidence Matrix

```
┌───────────────────────────────────────────────────┬──────────────────────────────────────────┬────────┐
│ Test Suite / Command                              │ Scope                                    │ Result │
├───────────────────────────────────────────────────┼──────────────────────────────────────────┼────────┤
│ pytest tests/hermes_cli/test_web_server.py        │ Web server REST & WebSocket routing      │ PASS   │
│ pytest tests/hermes_cli/test_dashboard_auth_gate. │ Auth gate & public URL exposure policy   │ PASS   │
│ npm test (electron/*.test.ts)                     │ Backend ownership & connection config    │ PASS   │
│ npm run typecheck (apps/desktop)                  │ Full Electron & React TypeScript check   │ PASS   │
│ npm run build (apps/desktop)                      │ Vite bundling & electron-main compilation│ PASS   │
│ Starlette TestClient Live Probes                  │ /api/config, /api/skills, /api/sessions  │ PASS   │
│ 9Router NUC Completions Probe                     │ http://192.168.100.110:20128/v1          │ PASS   │
└───────────────────────────────────────────────────┴──────────────────────────────────────────┴────────┘
```

---

## 5. Instructions for Independent Reviewer

1. Inspect staged changes: `git diff --cached`
2. Run backend tests:
   ```bash
   pytest tests/hermes_cli/test_web_server.py
   HERMES_DASHBOARD_PUBLIC_URL="" pytest tests/hermes_cli/test_dashboard_auth_gate.py
   ```
3. Run desktop tests & typecheck:
   ```bash
   cd apps/desktop
   npm test -- electron/backend-ownership.test.ts electron/connection-config.test.ts
   npm run typecheck
   ```
4. Verify that external security boundaries remain intact (requests from non-loopback IPs without valid session cookies still return 401).
5. Output formal review verdict (`APPROVE` or `REQUEST_CHANGES` with actionable findings).
