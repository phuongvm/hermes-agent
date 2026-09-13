# Backend review v1 — desktop-reconnect-session-resilience

Date: 2026-09-11
Reviewer: reviewer-agent4070
Kanban: t_2544a850
Verdict: **REJECTED — changes required**, not implementation approval.

## Scope / baseline

Reviewed working-tree delta from main HEAD `c06856e7892de5e80a0ba5b8a263338ae4269925`:
- hermes_cli/config_defaults.py (dashboard setting)
- hermes_cli/web_server.py (token resolution, readiness, middleware, startup flow)
- hermes_cli/dashboard_auth/middleware.py
- tests/hermes_cli/test_dashboard_session_resilience.py

Read both active delta specs, design.md, tasks.md, main dashboard-auth spec, root/hermes_cli AGENTS, surrounding authentication and initialization code, canonical runner and isolation setup, collaboration review mandate and lessons. Tasks 1.1–2.4 only are being evaluated. Current implementation uses `startup_grace_seconds = 30.0`; the handoff's earlier `startup_grace_period_sec = 5.0` is stale, not the current code.

## Findings summary

| Severity | Count | IDs |
|---|---:|---|
| CRITICAL | 2 | C1, C2 |
| WARNING | 3 | W1, W2, W3 |
| INFO | 1 | I1 |

## Code / security

### C1 — Concurrent first boot returns divergent credentials

- Spec: dashboard-auth delta, Persistent Dashboard Session Token Resolution, lines 4–20; design D1.
- Location: `hermes_cli/web_server.py:305–329` (shared temporary name at 316; truncation at 317; replace at 325; failure still returns generated token at 328).
- Issue: Both callers observe an absent final file, generate different tokens and write the SAME `.dashboard_session_token.tmp` with O_TRUNC. Publication is not a single-winner creation operation. A loser may return a token different from the stored token, so the process authenticates clients using a credential that cannot survive its next restart.
- Evidence: review probe forces two concurrent writers to reach publication after their writes. Real function and real temporary filesystem: **2 distinct returned tokens; only 1 of 2 callers matches persisted token**. No production token was read or printed.
- Required resolution: serialize durable token creation or use a safe single-winner publication protocol, with loser re-read; also handle regeneration races. Unique temporary names alone do not solve last-writer-wins credential divergence. Add deterministic concurrency regression coverage asserting every successful resolver returns the final persisted credential.

### C2 — Service-user-only file protection is not enforced on Windows

- Spec: dashboard-auth delta lines 4, 11; tasks.md 1.1 explicitly requires Unix 0600 / Windows ACL; design D1 and risk section.
- Location: `hermes_cli/web_server.py:305–308,317–324`.
- Issue: Creation supplies a POSIX mode and calls os.chmod only. Windows chmod does not establish a service-user-only DACL. The implementation neither sets a restricted Windows ACL nor verifies a restricted parent directory; existing nonempty files return immediately without permission checks. Consequently the required security property is unimplemented whenever the parent permits other principals to read the file. This review did not inspect production ACLs and does NOT claim an observed production secret exposure.
- Required resolution: use the project's supported secure credential-file mechanism with platform-native protection, or explicitly validate the parent protection before relying on inheritance; reject/repair insecure existing files according to an approved policy. Add native Windows ACL and Unix permission tests. Do not silently swallow protection failures and report persistence as fully secure.

### W1 — Loopback readiness gate does not cover valid authenticated requests

- Spec: dashboard-auth delta Startup Grace Period, lines 29–36; task 2.2.
- Location: `hermes_cli/web_server.py:711–725` versus `dashboard_auth/middleware.py:160–166`.
- Evidence: with readiness false, `/api/sessions` returns **503 without a token but 200 with the valid session token**. The gate is nested inside the invalid-token branch in loopback mode. In gated mode it runs before credential verification. There is no auth bypass: invalid credentials remain rejected after startup; this is a readiness-contract inconsistency.
- Required resolution: agree whether the requirement covers valid authenticated requests in both modes. Align code with the current specification, or obtain Designer approval for a narrower spec. Add valid-token-during-init coverage.

## Architecture / runtime validation

### W2 — Real startup recovery is not demonstrated by manually toggling readiness

- Spec: tasks 2.1–2.3; design D2 and G1.
- Location: `web_server.py:1061,1392,1461,1513–1527`; tests `test_dashboard_session_resilience.py:122–208`.
- Evidence: tests call set_startup_ready directly and use TestClient without lifespan. Production resets at start_server, awaits server.startup, then synchronously calls _on_server_started (ending in ready=True) before main_loop. The reviewed tests therefore demonstrate response selection for a forced state, not that real startup requests encounter the proposed protective window. Module import also sets readiness true.
- Required resolution: isolated startup lifecycle/integration evidence establishing the actual observable initialization interval, without touching live Gateway/Dashboard. Do not claim this fixes the reported restart storm based on these unit tests alone.

### W3 — Persistent process token does not establish OIDC session continuity

- Spec: design G2 / NG1 and main dashboard-auth spec.
- Location: `dashboard_auth/middleware.py:174–190,192–215`.
- Evidence: `_SESSION_TOKEN` is used for the loopback-process-token exception; remote bearer and cookie sessions pass through provider verification/refresh. Keeping `_SESSION_TOKEN` stable does not by itself prove remote OIDC sessions or WebSocket tickets survive restarts. This bounds the earlier RCA's claimed benefit, not a request to expand backend scope silently.
- Required resolution: keep process-token continuity and provider-session continuity separate in acceptance evidence; isolate/reproduce the originally reported remote failure path before declaring end-to-end remediation.

## Tests / evidence

Commands run from `O:/workspaces/oss/hermes-agent`:

1. `bash scripts/run_tests.sh tests/hermes_cli/test_dashboard_session_resilience.py`
   - Exit 0; `1 files, 11 tests passed, 0 failed` (6.1s).
2. `bash scripts/run_tests.sh -j 4 tests/hermes_cli/test_dashboard_auth_middleware.py tests/hermes_cli/test_dashboard_auth_401_reauth.py tests/hermes_cli/test_dashboard_token_auth.py`
   - Exit 0; `3 files, 59 tests passed, 0 failed` (3.9s).
   - Total: **70 tests passed across 4 files**. Runner estimate ~42 for the second command was not the actual result; summary 59 includes parametrization.
3. `.venv/Scripts/python.exe openspec/changes/desktop-reconnect-session-resilience/reviews/probe-reviewer-v1.py`
   - Exit 0 (diagnostic probe reports observations, not an acceptance-test pass):
   ```json
   {
     "concurrent_first_boot": {"distinct_returned_tokens": 2, "callers_matching_persisted_token": 1, "total_callers": 2},
     "loopback_during_initialization": {"missing_token_status": 503, "valid_token_status": 200, "ready": false},
     "loopback_after_initialization": {"invalid_token_status": 401, "valid_token_status": 200}
   }
   ```
4. `openspec validate desktop-reconnect-session-resilience --strict`
   - Exit 0; `Change 'desktop-reconnect-session-resilience' is valid`.
5. Scoped `git diff --check -- hermes_cli/config_defaults.py hermes_cli/web_server.py hermes_cli/dashboard_auth/middleware.py`
   - Exit 0, no output.
   - Whole-tree `git diff --check` exited 2 for pre-existing Markdown hard-break whitespace in agent_share.md lines 3,4,7. Not a code blocker. Chained status/validate did not execute in that first call; rerun separately.

The added suite does not cover concurrent creation, Windows ACL enforcement, persistence errors, actual process restart, or configuration override behavior. Passing 11/11 is reproducible, but insufficient for acceptance of the full claims.

## Coordination / DevOps

### I1 — Handoff and dashboard reconciliation

No Kanban task was supplied at start and queried reviewer queues were empty. During review the shared file gained task t_2544a850 and the stale Situation entry was corrected by another participant. This report now references the discovered card. No source or test implementation was modified by Reviewer. Only review artifacts and append-only Updates Log entries were written. No service restart, live config change, or live credential inspection was performed.

## Not reviewed / not verified

- Desktop/Electron implementation (groups 3–6), full suite, compiled desktop artifact and live multi-connection UI behavior: outside M1–M2 review scope.
- Actual Gateway/Dashboard restart, process-lifecycle integration and end-to-end recovery: not run; shared services must remain uninterrupted. Requires isolated harness or user-managed live operation.
- Native Unix permissions and production Windows ACLs: not exercised; C2 is a code-level missing-enforcement finding.
- Production logs and original RCA causal correlation: not independently audited in this review.

## Candidate fingerprints (SHA-256)

- config_defaults.py: a4b00f74ffe945570d1df2a9bf470bb352e16e427425f8d81099c5794a2ba0f6
- web_server.py: 088bcd1e3cfab3ea95b13f13482a85209cdcafe890f4db31c29678f4ae38f2ee
- dashboard_auth/middleware.py: ba4a71b2dd0fb2b080fbb26b2a05b6b8e3a6a2c51fdb46f8e8e004e170c4e809
- test_dashboard_session_resilience.py: 57eb15e874e0c287cc2b7f9147e0b429b95ee745d348d91486e83d50d4dc9d9e

## Handoff

Return C1/C2 to Coder; resolve W1–W3 with Designer/Leader as needed, then independently re-review. Completing the review-report task means the report is delivered, NOT that the backend implementation is approved. QA acceptance must remain gated on remediation and re-review.
