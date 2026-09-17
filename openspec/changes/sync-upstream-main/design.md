# Design: Evidence-Gated Upstream Main Reconciliation

## Context

This is an integration design, not a replacement auth architecture or an implementation report. Designer task `t_b79cb241` produces the four artifacts; Coder task `t_0eb436b3` imports them into the authorized integration worktree. Existing product requirements remain authoritative.

Pinned input revisions:
- Local L: `c57316beafaba4e27b9797e137430619f98a899d`.
- Upstream U: `24fd22b94df040d843eb280ff197a4bcd99a6fc3`.
- `git rev-list --left-right --count HEAD...upstream/main`: `175 2704`.
- `git merge-tree --write-tree --name-only HEAD upstream/main`: exit 1, 14 conflicted paths; preview tree `e5cd5e610c7efa43a6dab93200934f34817c3c96`. This command writes Git objects but does not merge the branch or modify the index/worktree. The preview tree contains unresolved content and is NOT an accepted candidate.

Leader explicitly authorized task invariants and canonical auth/reconnect specs as substitutes for missing `REQUIREMENTS.md`, and root/area AGENTS documentation for missing `ARCHITECTURE.md`. `website/docs/developer-guide/system-architecture.md` is absent at L; current `architecture.md` is supporting orientation only. This design does not fabricate the absent documents.

## Goals / Non-Goals

Goals: preserve all five local invariant groups; account for supplied and actual conflict inventories; retain compatible upstream fixes; require reproducible dependencies and exact-candidate independent evidence.

Non-goals: application implementation by Designer, blanket acceptance of upstream behavior, new credentials or schemas, production DB migration, release packaging/deployment, restarting Gateway/Dashboard/Desktop, touching main, or archiving this or any prerequisite change.

## Decisions

### D1. Preserve behavior, not entire sides of conflicted files

Use upstream organization when compatible, transplant protected local semantics at their new call sites, and maintain one owner per recovery operation. Never resolve a whole security-sensitive file with `ours`/`theirs` solely to remove markers. Local fixes may be replaced only after the resolution ledger maps local and upstream line ranges to the same requirement and empirical before/after tests. A clean auto-merge gets semantic review as well.

Rejected alternatives: retaining all local files discards unrelated upstream fixes; selecting all upstream files discards unproven local behavior; testing only conflicted files misses cross-file regressions.

### D2. Separate process-owned authentication from request-scoped work

Host auth provider registration and its session store bind to the process home resolved at startup. Routed profile context may select agent configuration, filesystem policy, or MCP discovery, but MUST NOT redirect the host auth store or replace the host-owned provider. An explicitly profile-launched server binds to that profile, not forcibly to root.

Preserve `web_server.py` startup readiness, persisted token precedence/permissions, and auth registry protection together with `plugins/dashboard_auth/self_hosted/`. Upstream threadpool/coalesced refresh work in `routes.py` may be retained, but test that moving a refresh off the event loop does not restore request-scoped session-store resolution. No new session table is designed here.

### D3. One native 401 recovery boundary

`main.ts` must route authoritative native bearer 401 recovery through one shared decision/refresher path. Retain `native-token-refresh.ts` force-refresh and rejected-bearer/generation checks, and the single-replay decision in `native-auth-decisions.ts`. Upstream recovery must not become an outer retry around a local retry.

Decision sequence: original request → authoritative 401 → consult current generation/rejected bearer → share in-flight refresh or reuse a newer bearer → replay at most once with a changed bearer → propagate replay result. A stale response cannot clear or overwrite a newer login. Do not reinterpret transport errors, 5xx, or policy 403 as credential rejection.

### D4. Reconnect and terminal rejection are different states

Preserve cached renderer state while disconnected; gate profile/session/config/model/tree background requests and coalesce resumption. An ordinary probe uses the existing same-endpoint consecutive-401 policy (at least two within 15 seconds, previously connected; success resets the counter). This is NOT the policy for authoritative current-session `/auth/native/refresh` terminal 401: that transition immediately marks the normalized endpoint signed-out/reauth-required, clears only obsolete matching credentials, and suppresses retry timers and polling until confirmed sign-in. Older generations and other endpoints remain unaffected.

Keep the one-global-reauth-modal behavior for pooled connections without treating a login to an unrelated gateway as successful authentication for every endpoint. Audit `auth-terminal-state.ts`, API client, preload/IPC, renderer wiring, background hooks and project-tree callers, even when they auto-merge.

### D5. Keep Buzz-specific liveness and delivery policy at the adapter boundary

Preserve actual local connection arguments and timeout constants in `plugins/platforms/buzz/adapter.py` unless an alternative is proved with responsive-idle, delayed-WAN, and dead-relay tests. A successful idle ping must keep the connection alive; failed/timed-out ping must enter bounded reconnect backoff. Record pre/post numeric timeout values in the ledger rather than inventing replacements.

Buzz keeps `SUPPORTS_MESSAGE_EDITING = False`; generic base/gateway changes must honor that capability while preserving editing for other adapters. Assert final content is delivered once and no frozen edit-dependent preview remains. Adopt compatible upstream delivery/ledger improvements, not global editing disablement.

### D6. Distinguish foreign JWKS keys from provider outages; preserve native chooser

Retain the active `fix-multi-provider-jwks-kid-classification` delta in the self-hosted verifier: healthy valid JWKS with absent `kid` means this provider cannot verify the token; network/structural JWKS failures remain provider errors. Do not move this distinction into HTML rendering or weaken signature/issuer/audience/subject/TTL checks.

`routes.py` and `login_page.py` are one chooser contract: validate S256 and loopback redirect before broker allocation; preserve escaped provider names, prefix, PKCE inputs, state, no-store headers, and explicit selection. Canonical `dashboard-auth` requires the non-password brokerable-provider chooser and single eligible provider auto-selection. Upstream broadens selection to password providers; do not silently adopt that as equivalent. Preserve canonical eligibility by default. Any intentional expansion requires separate approved spec adjustment and OAuth-only, password-only, and mixed-provider evidence. Preserve compatible upstream off-event-loop refresh and singleflight without merging their availability policy into terminal 401 classification.

### D7. Dependency isolation is an acceptance boundary

Reconcile manifests before lock regeneration. Inspect root `package.json` workspaces and actual lock topology; root `package-lock.json` is the supplied npm lock path. Do not invent an `apps/desktop/package-lock.json`. Keep Python and npm tool versions in evidence, regenerate only affected locks, and prove clean installs from final locks.

The dashboard records node_modules junctions pointing into the live checkout. These are NOT an isolated dependency environment. Before installs/builds, resolve junction targets and obtain a safe isolated dependency directory. Do not delete through junctions or run npm/uv mutations against shared live directories. Never install into the running service environment. A blocked install or missing toolchain is an explicit blocker, not permission to borrow stale dependencies or fabricate a pass.

## Conflict Resolution Matrix

Status `C` means an actual merge-tree conflict at L/U. `S` means supplied review target, not in the actual conflict set. `A` means ancillary dependency target. This table retains all 14 supplied paths, including absent/moved candidates; it does not claim every supplied path exists or conflicts.

| ID | Supplied path | Observed | Resolution strategy and required evidence |
|---|---|---|---|
| S01 | `apps/desktop/electron/main.ts` | C | Integrate upstream IPC/connection changes around D3–D4. Preserve one recovery owner, generation checks, signed-out propagation and accurate HTTP status. Test original/replay counts, stale login race, multiple endpoints and reconnect. |
| S02 | `apps/desktop/electron/native-auth-decisions.ts` | C | Retain single replay decision; reconcile upstream classification without a nested refresh loop. Test force refresh before expiry, changed/unchanged bearer, replayed 401 and non-401. |
| S03 | `apps/desktop/electron/native-auth-decisions.test.ts` | C | Union meaningful local and upstream assertions; resolve helper API changes, never delete failing invariant tests to obtain green. Add combined call-count and stale-generation coverage if absent. |
| S04 | `apps/desktop/src/store/profile.ts` | C | Combine upstream store behavior with disconnected cache retention, terminal suspension and once-only recovery. Test focus/reconnect/sign-in and stable-connection error reporting. |
| S05 | `apps/desktop/src/app/session/hooks/use-model-controls.ts` | C | Preserve upstream model-selection behavior with disconnected/terminal auth gates and coalesced model refresh. Retain matching hook tests that auto-merged. |
| S06 | `apps/desktop/src/app/session/sidebar.tsx` | S | Not a merge-tree conflict. Establish existence/move status via `git ls-tree` at both refs; trace actual profile rail/sidebar callers before editing. Preserve focus suppression and sign-in recovery at current locations; record any absent path rather than create a replacement file. |
| S07 | `apps/desktop/package.json` | S, auto-merged | Review resulting scripts/dependency versions against local Desktop needs and upstream requirements; resolve manifest first, regenerate real root lock, verify isolated install, Electron/renderer types and Desktop build. |
| S08 | `gateway/platforms/base.py` | C | Retain upstream compatible proxy/media/delivery changes while preserving adapter editing capability behavior. Test Buzz non-editing and another editable adapter; protect profile-scoped secrets and media deny rules. |
| S09 | `gateway/run.py` | S, auto-merged | Trace streaming/final delivery and profile ownership call sites against merged base/adapter API; preserve Buzz final-once semantics and unrelated adapters. Run affected gateway tests even without markers. |
| S10 | `hermes_cli/dashboard_auth/routes.py` | C | Reconcile D2 and D6: canonical chooser eligibility, PKCE safety, host-bound provider refresh, upstream singleflight/threadpool behavior. Test multi-provider routing and outages separately from invalid sessions. |
| S11 | `hermes_cli/dashboard_auth/login_page.py` | C | Keep one chooser renderer with escaping, retained PKCE/state/prefix and cache protection; align caller signature with routes. JWKS classification belongs in verifier, not this page. |
| S12 | `hermes_cli/web_server.py` | S, auto-merged | Audit host-owned provider initialization, token persistence and 503 readiness after merge; test root and named process homes plus forced/concurrent rediscovery and restart. |
| S13 | `website/docs/developer-guide/system-architecture.md` | S, absent at L | Do not manufacture this source. Use current `architecture.md` and area AGENTS for orientation; document final architecture changes at verified actual paths. This is not the actual documentation relocation conflict (N05). |
| S14 | `tests/hermes_cli/test_dashboard_auth.py` | S | Not a merge-tree conflict. Resolve actual auth test suite layout with tracked-file inventory; retain coverage across `test_dashboard_auth*.py`, especially actual conflicted native-flow test N04. Do not create a placeholder test file to match the supplied name. |

### Additional observed conflicts and ancillary paths

These five N rows are actual conflicts outside the 14 supplied paths. A02 was supplied as ancillary but is also an actual conflict. Coder must obtain Leader scope confirmation before resolving the newly discovered areas; the table defines their proposed safety boundary, not silent scope authorization.

| ID | Path | Observed | Resolution strategy and required evidence |
|---|---|---|---|
| N01 | `hermes_cli/kanban_db.py` | C | Merge lifecycle changes with local structured handoff/artifact safety. Trace upstream worker PID/start fingerprint, stale claim accounting, parent gating and review artifact persistence into helpers/migrations. Test disposable DB migration, parent gating, review rollback/artifact survival, stale-worker identity and delegated-child restrictions; never operate on live Kanban DB as a test. |
| N02 | `hermes_cli/mcp_startup.py` | C | Preserve per-profile discovery slots and copied context/secret scope; integrate existing startup/deferred/wait behavior without global first-profile lockout. Test simultaneous profiles, empty/no-server config, retry after failed discovery and correct context in worker thread. Auth provider ownership remains process-scoped despite MCP being profile-scoped. |
| N03 | `hermes_cli/web_routers/files.py` | C | Retain centralized sensitive-file checks and secure path resolution across list/read/download/data-url while preserving remote preview functionality. Test traversal, symlink escape, secrets denial, allowed regular files, MIME/size rules and routed-profile boundaries using disposable files. Existing `remote-fs-security` and `desktop-remote-file-preview` contracts remain binding. |
| N04 | `tests/hermes_cli/test_dashboard_auth_native_flow.py` | C | Reconcile local chooser assertions and upstream refresh/singleflight tests without accepting password-provider semantic expansion by accident. Preserve PKCE/redirect/explicit selection, no-cache, root/named-provider session and outage tests; document intentional assertion changes against D6. |
| N05 | `website/docs/developer-guide/kanban-openspec.md` | C, file location | Preview reports local `docs/kanban-openspec.md` added under an upstream-renamed directory. Preserve local content at upstream documentation location; inspect links/navigation and avoid duplicate old/new authority. Validate resulting links/build through existing website tooling. |
| A01 | `package-lock.json` | A, auto-merged | Regenerate from reconciled workspace manifests with selected npm version; isolated clean install and no unexplained broad dependency churn. |
| A02 | `pyproject.toml` | C, ancillary | Integrate required local extras with upstream version/dependency/security updates. Confirm Python constraint and mutually compatible pins; parse and lock with chosen uv version, then test clean isolated install. |
| A03 | `uv.lock` | A, auto-merged | Regenerate only after pyproject resolution; run lock consistency and isolated locked sync for required extras. Never accept textual auto-merge alone as lock correctness. |

### Invariant-critical non-conflicted dependencies

Review at minimum: `plugins/dashboard_auth/self_hosted/`, auth provider registry and middleware; `apps/desktop/electron/native-token-refresh.ts`, `auth-terminal-state.ts` at its tracked location, preload/IPC declarations, API client, renderer background/config/project-tree callers; `plugins/platforms/buzz/adapter.py` and its tests. Expand to callers of changed functions. Do not assume a similarly named file is authoritative without tracing imports/usages.

## Evidence and Verification Plan

Resolution ledger location for Coder: `openspec/changes/sync-upstream-main/reviews/resolution-ledger.md`. Each row: matrix ID and actual path; L/U/candidate identities and line ranges; chosen strategy; invariant/spec scenario; exact test command; before/after output and exit; unresolved risks. For auto-merges record audited semantic delta, not merely “clean.” New tests are Coder work; no test is claimed executed by this design.

| Gate | Minimum evidence |
|---|---|
| G1 / SYNC-1–2 | Correct integration branch/path, pinned refs, inventory accounting, Leader scope confirmation for N01–N05, imported artifact hashes and dependency isolation. |
| G2 / SYNC-3 | Root/named process homes, routed and concurrent rediscovery, session DB location, valid refresh, restart token continuity, 503/Retry-After then normal valid/invalid auth. Disposable identities only. |
| G3 / SYNC-4 | Unexpired 401 force refresh, one replay, concurrent/staggered shared rotation, unchanged bearer, replayed 401, stale failure after login and non-auth failure classification. Assert network call counts. |
| G4 / SYNC-5 | Disconnected request count zero; cached state retained; coalesced reconnect; two-in-15s ordinary probe latch/reset; immediate terminal refresh suspension; no timers/network after terminal state; endpoint/generation isolation; confirmed sign-in resumes once. |
| G5 / SYNC-6 | Actual Buzz connection arguments and before/after values, idle responsive ping, delayed WAN and dead relay, bounded backoff, non-editable final-once behavior, editable adapter control case. |
| G6 / SYNC-7 | Foreign kid versus broken/unreachable JWKS; later provider success; invalid signature/claims denial; multi/single/explicit native chooser, escaped PKCE/state/prefix/no-store and mixed/password cases per D6. |
| G7 / N01–N05 | Kanban disposable migration/lifecycle, MCP profile isolation, filesystem security/preview and moved-doc link checks. |
| G8 / SYNC-8 | Locked isolated installs; Desktop affected Vitest tests, Electron and renderer typechecks and build; affected backend/gateway tests; independent Reviewer and QA on the same candidate. |

Discover exact test selectors from the reconciled repository; known entry points include `apps/desktop/electron/native-auth-decisions.test.ts`, `apps/desktop/src/store/profile.test.ts`, `apps/desktop/src/app/session/hooks/use-model-controls.test.tsx`, `tests/hermes_cli/test_dashboard_auth_native_flow.py`, `tests/hermes_cli/test_mcp_startup.py`, `tests/hermes_cli/test_web_server_fs.py`, and `tests/gateway/test_buzz_adapter.py`. Include all related auth and lifecycle suites discovered by Coder. Missing coverage requires regression tests, not a skip presented as success. Run declared package scripts where present; where `typecheck` is absent, record and run the actual Electron and renderer TypeScript project commands. Record dependency/network blockers and skip counts explicitly.

## Migration / Handoff / Rollback

1. Designer publishes artifact hashes and validation result from `.worktrees/t_b79cb241`; Leader/Coder copies the four artifact types and `.openspec.yaml` into the integration worktree without overwriting a divergent artifact set. Verify hashes and rerun strict validation there. No application source is transferred from Designer.
2. Coder records a clean baseline or exact pre-existing dirty manifest, confirms branch and refs, and resolves N-area authorization plus junction isolation before merging. No silent advancement of `upstream/main`.
3. Resolve in coherent groups: manifests/toolchain, backend auth, Desktop recovery, gateway/Buzz, newly authorized areas and documentation. Update ledger after each group; retain all local invariant tests.
4. Pin candidate identity before Reviewer, then QA. Any further code/dependency edit invalidates affected evidence and requires rerun.
5. Do not deploy or mutate production data. A failed candidate remains isolated; preserve failure evidence and use normal scoped Git recovery only with verified ownership of edits, never destructive cleanup of shared state. Commander decides main integration and any production rollback. No archive here.

## Risks / Open Questions

- Confirm newly observed conflict scope with Leader before Coder resolves N01–N05; count equality does not imply inventory equality.
- Password-inclusive upstream chooser differs from canonical eligibility; D6 selects preservation. Broader behavior needs an explicit approved change, not conflict-resolution convenience.
- Shared node_modules junctions undermine isolation; installs/builds are gated until proven safe.
- Large upstream divergence means the matrix is a minimum review surface, not a guarantee that only those files changed.
- Worktree-local artifact/log completion does not synchronize the canonical dashboard; Leader must import artifacts and sync progress. Overall merge and Commander approval remain pending.

## Source / Query Appendix

All sources are repository files or task instructions, not external claims. Reproduce from the Designer worktree at L/U:
- `git rev-parse HEAD upstream/main`; `git rev-list --left-right --count HEAD...upstream/main`.
- `git merge-tree --write-tree --name-only HEAD upstream/main` (exit 1 expected for these conflicts; names precede the blank line).
- `git diff HEAD...upstream/main -- <path>` for upstream deltas; also inspect `git diff upstream/main...HEAD -- <path>` for local-side decisions before implementation. Triple-dot diff is one side since merge-base, not a complete equivalence proof.
- `git ls-tree HEAD -- <supplied-path>` and `git ls-tree upstream/main -- <supplied-path>` for missing/moved claims; follow verified replacements.
- Canonical sources: `openspec/specs/dashboard-auth/spec.md`, `desktop-reconnect-resilience/spec.md`, `buzz-websocket/spec.md`, `remote-fs-security/spec.md`, `desktop-remote-file-preview/spec.md` under the same specs directory.
- Active preservation inputs: `openspec/changes/desktop-native-401-single-replay/` and `openspec/changes/fix-multi-provider-jwks-kid-classification/` (designs and delta specs).
- Architecture sources: root `AGENTS.md`, area guides in `apps/desktop`, `gateway`, `hermes_cli`, and `website/docs/developer-guide/architecture.md`.
- Verification of this specification: `openspec validate sync-upstream-main --strict`; artifact status via `openspec status --change sync-upstream-main --json`. Actual output belongs in `verification.md` and Kanban metadata, not a claim of product test success.
