# Independent Code Review — TS1294 Reconnect-Backoff Build Fix and Memory Provider Scoping (`sync-upstream-main` v4)

## Verdict

**APPROVED** for integration and downstream QA sign-off.

Independent review and empirical verification confirm that:
1. The TypeScript TS1294 syntax error ('erasableSyntaxOnly' incompatibility with constructor parameter properties) in `apps/shared/src/reconnect-backoff.ts` is cleanly resolved by explicitly declaring properties on the class body and assigning them within the constructor. Web UI production compilation is restored (`BUILD RESULT: True`, built in 2.69s), all TypeScript typechecks pass cleanly across `@hermes/shared`, `web`, and `apps/desktop` (3 tsconfigs), and all Reconnect Backoff / Web / Desktop reconnect test suites pass 100%.
2. The `UnscopedSecretError` during memory provider schema discovery in `hermes_cli/web_server_memory.py` under gateway multiplexing is properly resolved using `_ensure_active_profile_scope()` and a safe fallback with `set_secret_scope({})`. Unhandled warning tracebacks in `errors.log` are eliminated while safely returning provider config schemas without credential leakage. All unit and integration memory tests pass 100%.

Reviewer executed no code fixes and performed no unauthorized commits, service restarts, or external database modifications.

---

## Technical Audit & Verification Evidence

### 1. Reconnect-Backoff TS1294 Fix & Web UI Build (`apps/shared/src/reconnect-backoff.ts`)

- **Analysis**: TypeScript 5.8+ and modern type-stripping runtimes with `--erasableSyntaxOnly` disallow constructor parameter properties (`constructor(private readonly x = ...)`) because parameter properties cannot be stripped as pure type annotations.
- **Remediation**:
  - Class properties `maxAttempts`, `maxDurationMs`, and `streakResetMs` are explicitly declared on `ReconnectBackoffTracker`.
  - Assigned in the constructor body (`this.maxAttempts = maxAttempts`, etc.).
  - Default parameters (`MAX_RECONNECT_ATTEMPTS`, `MAX_RECONNECT_DURATION_MS`, `STREAK_RESET_WINDOW_MS`) and state machine invariants are preserved identically.
- **Empirical Evidence**:
  - **@hermes/shared Typecheck**:
    ```text
    npm run typecheck --workspace=@hermes/shared
    => tsc -p . --noEmit
    => exit 0 (0 errors)
    ```
  - **Web Typecheck**:
    ```text
    npm run typecheck --workspace=web
    => tsc -p . --noEmit
    => exit 0 (0 errors)
    ```
  - **Desktop Typecheck (3 tsconfigs)**:
    ```text
    cd apps/desktop && npm run typecheck
    => tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit && tsc -p tsconfig.e2e.json --noEmit
    => exit 0 (0 errors)
    ```
  - **Web UI Production Build**:
    ```text
    python -c "from hermes_cli.main_web_build import _build_web_ui; from pathlib import Path; print('BUILD RESULT:', _build_web_ui(Path('web'), fatal=True))"
    => → Building web UI...
    => ✓ 2241 modules transformed.
    => ✓ built in 2.69s
    => ✓ Web UI built
    => BUILD RESULT: True
    => exit 0
    ```
  - **Vitest Suites**:
    - `npx vitest run apps/shared/src/reconnect-backoff.test.ts`: **1 file, 10/10 tests passed** (156ms, exit 0)
    - `npm run test --workspace=web`: **47 files, 333/333 tests passed** (3.33s, exit 0)
    - `cd apps/desktop && npm run test -- src/store/gateway-reconnect.test.ts`: **1 file, 6/6 tests passed** (2.17s, exit 0)

---

### 2. Memory Provider Schema Scoping & Safe Fallback (`hermes_cli/web_server_memory.py`)

- **Analysis**: In gateway multiplexing environments (`is_multiplex_active() == True`), memory providers like `mem0` query `secret_scope.get_secret("MEM0_MODE", "")` during `provider.get_config_schema()`. When invoked on threads without an active profile secret scope, an `UnscopedSecretError` was raised, logging warning tracebacks into `errors.log` and preventing schema retrieval.
- **Remediation**:
  - Added `@contextlib.contextmanager def _ensure_active_profile_scope()`:
    - Resolves process home vs active profile home.
    - Activates profile secret scope via `launch_secret_scope()` or `build_profile_secret_scope()`.
    - Resets context scope token cleanly in `finally:`.
  - Added safe fallback handling in `_normalize_memory_provider_schema()`:
    - Catches `UnscopedSecretError`.
    - If scope was inactive, logs at `DEBUG` level (avoiding polluting `errors.log`).
    - Enters safe empty fallback scope `set_secret_scope({})` to evaluate `provider.get_config_schema()` and resets token in `finally:`.
    - No credential exposure: fallback scope is explicitly empty (`{}`).
- **Empirical Evidence**:
  - **Bytecode Compilation**:
    ```text
    python -m py_compile hermes_cli/web_server_memory.py tests/hermes_cli/test_web_server_memory_schema_scope.py
    => exit 0
    ```
  - **Regression Test Suite**:
    ```text
    bash scripts/run_tests.sh tests/hermes_cli/test_web_server_memory_schema_scope.py
    => Discovered 1 test files (~2 tests)
    => [100.0% | 2/~2 | ✓2 | ✗0] ✓ tests\hermes_cli\test_web_server_memory_schema_scope.py (2✓, 2.5s)
    => Summary: 1 files, 2 tests passed, 0 failed in 2.5s (56 workers)
    => exit 0
    ```
  - **Comprehensive Memory Test Suites**:
    ```text
    bash scripts/run_tests.sh \
      tests/hermes_cli/test_memory_status.py \
      tests/hermes_cli/test_memory_setup.py \
      tests/hermes_cli/test_web_memory_providers_honcho_write.py \
      tests/plugins/memory/test_config_schema.py \
      tests/plugins/memory/test_mem0_providers.py \
      tests/plugins/memory/test_multiplex_memory_identity_scope.py
    => Discovered 6 test files (~24 tests)
    => [100.0% | 24/~24 | ✓24 | ✗0]
    => Summary: 6 files, 24 tests passed, 0 failed, 1 skipped in 2.2s (56 workers)
    => exit 0
    ```

---

## SDLC Multi-Perspective Assessment

1. **Requirements & Correctness**: The changes address the exact root causes of the build failure and the runtime warning tracebacks without scope creep.
2. **Architecture & Scope Discipline**: Both fixes are narrowly scoped to their respective modules (`reconnect-backoff.ts` and `web_server_memory.py`). Invariants across Gateway multiplexing and Desktop reconnect resilience are preserved.
3. **Security & Secrets Handling**: The fallback secret scope utilizes an empty dictionary (`{}`), ensuring that no secrets or environment variables leak during unauthenticated schema queries. Context tokens are guaranteed to reset in `finally:` blocks.
4. **DevOps & Build Integrity**: Web UI builds deterministically (`BUILD RESULT: True`) with fresh assets emitted to `hermes_cli/web_dist/`. Typechecking across the entire monorepo is completely green.

---

## Conclusion & Recommendation

Both fixes satisfy all verification gates and are **APPROVED**.
The candidate changes in `apps/shared/src/reconnect-backoff.ts`, `hermes_cli/web_server_memory.py`, and `tests/hermes_cli/test_web_server_memory_schema_scope.py` are verified and ready for downstream QA sign-off.
