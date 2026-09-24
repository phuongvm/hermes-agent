# Independent Convergence Re-review — `sync-upstream-main` v3

## Verdict

**REQUEST CHANGES / NOT APPROVED FOR FAST-FORWARD** on exact candidate `43ca20a5fd25ec415315bc93ca89370d4fd872b9`.

The three convergence conflicts are resolved coherently and the five protected invariant groups remain green in focused verification. However, the canonical Python verification exposed one reproducible repository test failure in `tests/hermes_cli/test_web_server.py::TestBuildSchemaFromConfig::test_no_single_field_categories`: merged `CONFIG_SCHEMA` has a singleton `fs` category. Therefore the candidate does not satisfy the clean-suite acceptance gate in SYNC-8 and cannot be recommended for fast-forward yet.

Reviewer made no implementation changes and performed no service restart, deployment, database write, or branch integration.

## Candidate identity and merge topology

```text
git rev-parse 43ca20a5fd^1 43ca20a5fd^2
=> 895a2838539492539647df52735ac6b70f64cabf
   79b3d6d9c8b895dd4baad4f579218c372ff3c025

git merge-base --is-ancestor 79b3d6d9c8 43ca20a5fd
=> exit 0
```

The candidate is a true descendant of `origin/main` target `79b3d6d9c8` and is structurally fast-forwardable. `git diff --check 895a283853..43ca20a5fd` exited 0. Review worktree was clean before and after verification.

## Conflict-resolution audit

### 1. `apps/desktop/src/lib/reconnect-backoff.ts` modify/delete — PASS

The stale Desktop-local implementation is absent. Git recognizes an 87% rename into `apps/shared/src/reconnect-backoff.ts`. The shared implementation retains the `79b3d6d9c8` equal-jitter algorithm, Retry-After lower bound, 12-attempt/5-minute exhaustion, and 30-second successful-ping reset, while preserving the upstream shared module's deterministic `jitter: false` mode and integer attempt normalization (`apps/shared/src/reconnect-backoff.ts:18-125`). Public exports are retained at `apps/shared/src/index.ts:107-116`.

### 2. `apps/desktop/src/store/gateway-reconnect.ts` content conflict — PASS

The candidate retains `79b3d6d9c8` owner-key normalization, endpoint-scoped terminal sign-out refusal, and per-owner in-flight coalescing (`apps/desktop/src/store/gateway-reconnect.ts:4-68`). It also preserves the integration branch's localized toast reconnect action (`:70-76`). Six focused behavior tests pass.

### 3. `apps/shared/src/reconnect-backoff.test.ts` content conflict — PASS

The merged suite covers equal-jitter floor and ceiling, saturation, Retry-After, reset semantics, negative attempts, deterministic no-jitter mode, stable-open reset, and exhaustion (`apps/shared/src/reconnect-backoff.test.ts:11-144`). Ten tests pass.

## Protected invariant mapping

- **SYNC-3 process-home authentication ownership:** dashboard-auth canonical matrix passed 200/200 and plugin provider matrix passed 142/142.
- **SYNC-4 native bearer recovery:** `native-auth-decisions.test.ts` + `native-access-token.test.ts` passed 35/35.
- **SYNC-5 terminal-auth suspension/reconnect:** `gateway-reconnect.test.ts` passed 6/6; `profile.test.ts` + `auth-terminal-state.test.ts` passed 30/30; `use-gateway-boot.test.tsx` passed 55/55.
- **SYNC-6 Buzz liveness/non-editing delivery:** canonical wrapper included all Buzz files and passed their 266 tests; combined canonical run passed all selected tests.
- **SYNC-7 multi-provider auth/JWKS semantics:** canonical dashboard-auth and plugin-auth matrices passed as above.
- **`79b3d6d9c8` event-loop isolation:** `tests/hermes_cli/test_web_server_sessiondb_eventloop.py` passed 3/3; compute-host focused files passed 34/34 directly and in the canonical combined run.

## Independent execution evidence

### Canonical affected Python verification

```text
export HERMES_PYTHON=O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe
scripts/run_tests.sh \
  tests/tui_gateway/test_compute_host.py \
  tests/tui_gateway/test_compute_host_phase1.py \
  tests/tui_gateway/test_compute_host_turn_protocol.py \
  tests/gateway/test_buzz_*.py \
  tests/hermes_cli/test_dashboard_auth_*.py \
  tests/plugins/dashboard_auth/
=> 31 files, 632 tests passed, 0 failed; exit 0
```

### Desktop/Shared verification

```text
npx vitest run apps/shared/src/reconnect-backoff.test.ts
=> 1 file, 10 tests passed; exit 0

cd apps/desktop
npm test src/store/gateway-reconnect.test.ts
=> 1 file, 6 tests passed; exit 0

npm test src/app/gateway/hooks/use-gateway-boot.test.tsx
=> 1 file, 55 tests passed; exit 0

npm test electron/native-auth-decisions.test.ts electron/native-access-token.test.ts
=> 2 files, 35 tests passed; exit 0

npm test src/store/profile.test.ts src/store/auth-terminal-state.test.ts
=> 2 files, 30 tests passed; exit 0

npm run typecheck
=> all three TypeScript projects clean; exit 0
```

A root-level direct Vitest invocation for the Desktop test initially failed to resolve the `@/i18n` alias. Re-running through the Desktop package's declared `npm test` command passed; this was command-context error, not a candidate defect.

## Blocking finding

### C1 — Canonical web-server suite fails on merged config schema

**Spec:** SYNC-8 requires affected behavioral tests and exact-candidate independent evidence; a failing required suite blocks acceptance (`openspec/changes/sync-upstream-main/specs/upstream-sync-preservation/spec.md:134-147`).

**Reproduction:**

```text
export HERMES_PYTHON=O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe
scripts/run_tests.sh \
  tests/hermes_cli/test_web_server_sessiondb_eventloop.py \
  tests/hermes_cli/test_web_server.py
=> 188 passed, 1 failed, 4 skipped; exit 1
```

Failure:

```text
tests/hermes_cli/test_web_server.py:2522
AssertionError: Category 'fs' has only 1 field(s) — should be merged
```

The event-loop regression file itself passed 3/3. The failure is deterministic in the adjacent canonical web-server suite and indicates the merged configuration schema violates its own category-shape invariant. It was not caused by the three hand-resolved reconnect files, but it exists on the exact fast-forward candidate and invalidates a clean acceptance claim.

**Minimum remediation:** reconcile the `fs.hidden_dirs` schema category with the repository's category contract (or, if the contract is intentionally obsolete, change it through an explicitly justified product/test decision), then rerun the failing file plus the canonical 632-test matrix, Desktop focused suites, and typecheck on the new exact commit.

## Conclusion

The convergence mechanics, backoff behavior, event-loop isolation, and all five protected invariants are independently verified. The exact candidate is nevertheless **not approved** because one canonical repository test remains red. Fast-forward and deployment remain pending remediation, re-review, QA, and Commander approval.
