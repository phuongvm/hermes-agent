# Independent Code Review — Packaged Desktop Runtime Version Resolution

## Verdict

**APPROVED FOR DOWNSTREAM QA, WITH NON-BLOCKING BASELINE CAVEATS.**

The scoped implementation satisfies the approved packaging-mode priority contract:

- Packaged mode (`isPackaged === true`) resolves `installStamp` before `updateRoot` and does not probe the source tree when the stamp is valid.
- Development mode (`isPackaged === false` or omitted) preserves source-tree-first behavior.
- The production call site passes the existing real packaging flag, `IS_PACKAGED`.
- IPC response shape, `hermesRoot`, `resolveUpdateRoot()`, stamp validation/loading, and bundle-skew inputs remain unchanged.

No blocking correctness, security, typing, or scope defect was found in the three implementation files. The Reviewer made no implementation fixes.

This verdict advances the candidate to the pre-created QA task; it is **not** archive approval. OpenSpec task 4.4 (installed Windows runtime observation) remains incomplete and must be completed by downstream QA/re-package verification.

## Review Context

- Change: `desktop-packaged-version-resolution`
- Review task: `t_70a93768`
- Source scope:
  - `apps/desktop/electron/runtime-version.ts`
  - `apps/desktop/electron/main.ts`
  - `apps/desktop/electron/runtime-version.test.ts`
- Contract sources:
  - `proposal.md`
  - `design.md` decisions D1–D5
  - `specs/desktop-runtime-version/spec.md` (5 requirements / 13 scenarios)
  - `tasks.md` (19 complete / 20 total; task 4.4 open)
- Architecture sources:
  - `AGENTS.md`
  - `apps/desktop/AGENTS.md`
  - `apps/desktop/DESIGN.md`
  - `website/docs/developer-guide/architecture.md`

The repository does not contain a dedicated `code-review-mandate/spec.md` or project `lessons-learned.md`. The available review mandates (`rules/skills/code-review.md`, `rules/skills/code-review-expert.md`) and lessons template were loaded as bounded substitutes.

## Three-Dimension Scorecard

| Dimension | Status | Evidence |
|---|---|---|
| Completeness | PASS for code review; runtime task pending | 19/20 tasks complete. All 5 requirements and 13 scenarios map to code/tests. Manual installed-build task 4.4 remains open. |
| Correctness | PASS | Packaged and development orderings match design D2; focused resolver/status tests pass 33/33; all three TypeScript configurations pass. |
| Coherence | PASS | Explicit DI input preserves resolver purity; one branch selects an evaluator order; production wiring uses the existing canonical `IS_PACKAGED`; no renderer or IPC contract change. |

## Requirement and Scenario Mapping

### 1. Packaging mode is explicit and defaults to development order

- `VersionResolutionContext.isPackaged?: boolean`: `apps/desktop/electron/runtime-version.ts:158-166`
- Explicit strict branch (`ctx.isPackaged === true`): `apps/desktop/electron/runtime-version.ts:233-246`
- Omitted/false flag coverage: `apps/desktop/electron/runtime-version.test.ts:546-603`
- The resolver performs no environment or Electron probing; it consumes only the supplied context.

**Assessment:** Satisfies “Version Resolution Context Declares Packaging Mode.”

### 2. Packaged mode prioritizes the bundled stamp

- Packaged evaluator order is stamp → source → app version: `apps/desktop/electron/runtime-version.ts:236-241`
- Stamp validity gate remains version + short commit: `apps/desktop/electron/runtime-version.ts:199-207`
- Valid packaged stamp wins without source existence/read calls: `apps/desktop/electron/runtime-version.test.ts:363-402`
- Dirty formatting: `apps/desktop/electron/runtime-version.test.ts:404-426`
- Missing/invalid stamp fallback to source and app version: `apps/desktop/electron/runtime-version.test.ts:428-544`

**Assessment:** Satisfies all five packaged-mode scenarios, including the no-source-probe invariant.

### 3. Development mode remains source-tree-first

- Development evaluator order is source → stamp → app version: `apps/desktop/electron/runtime-version.ts:242-246`
- Explicit false and omitted flag coverage: `apps/desktop/electron/runtime-version.test.ts:546-603`
- Pre-existing source/stamp/app fallback tests remain intact above the new test block.

**Assessment:** Development workflow is preserved.

### 4. Production supplies the canonical packaging flag

- Canonical flag includes Electron and verification override state: `apps/desktop/electron/main.ts:538`
- Single production resolver wiring: `apps/desktop/electron/main.ts:17643-17650`
- About panel and IPC continue to call the same resolver: `apps/desktop/electron/main.ts:17666-17699`
- Renderer version refresh receives `hermes:version`: `apps/desktop/src/store/updates.ts:353-378`
- Status bar supplies `desktopVersion.appVersion` to `resolveVersionStatus`: `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx:345-360`
- Remote client label formats the passed version: `apps/desktop/src/lib/version-status.ts:80-87`

**Assessment:** Production wiring and status-bar derivation are coherent. The installed-binary observation remains for QA task 4.4.

### 5. Sibling contracts remain unchanged

The `main.ts` diff is limited to the adjacent resolver comment and one object property (`isPackaged: IS_PACKAGED`). The `hermes:version` return object at `apps/desktop/electron/main.ts:17683-17699` is unchanged, including `hermesRoot` and bundle-skew fields. `resolveUpdateRoot()`, `loadInstallStamp()`, validation functions, renderer code, and IPC types were not modified by this candidate.

**Assessment:** Satisfies the non-regression contract.

## Scope and Code Quality Audit

- Scoped implementation diff: exactly three files named in the task.
- `main.ts` hunk: confined to `resolveHermesVersion()` and its adjacent comment.
- Implementation shape follows design D3: private `string | null` rung evaluators and one packaging-mode ordering decision.
- Source read failure remains fail-soft through the existing `try/catch` fallback semantics.
- `git diff --check` on the three implementation files: exit 0.
- Focused ESLint on the two changed resolver/test modules: 0 errors, 4 formatting warnings. These warnings do not affect behavior or type safety.
- Linting the full `main.ts` also reports three existing import-order errors at unrelated imports and unrelated warnings; the candidate does not modify those imports/lines.

The shared checkout contains unrelated modified/untracked files (including `package-lock.json` and `apps/desktop/package.json`). They are outside this task and were not attributed to this candidate. Review conclusions are based on the scoped three-file diff.

## Independent Execution Evidence

### Focused resolver and status derivation tests

```text
cd apps/desktop
npx vitest run electron/runtime-version.test.ts src/lib/version-status.test.ts

Test Files  2 passed (2)
Tests       33 passed (33)
Duration    2.84s
Exit        0
```

This covers all 22 resolver/stamp tests plus 11 status derivation tests.

### TypeScript typecheck

```text
cd apps/desktop
npm run typecheck

> tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit && tsc -p tsconfig.e2e.json --noEmit
Exit 0
```

### Strict OpenSpec validation

```text
openspec validate desktop-packaged-version-resolution --strict
Change 'desktop-packaged-version-resolution' is valid
Exit 0
```

### Diff integrity

```text
git diff --check -- \
  apps/desktop/electron/runtime-version.ts \
  apps/desktop/electron/main.ts \
  apps/desktop/electron/runtime-version.test.ts

Exit 0
```

## Broader Electron Baseline Observation

The Reviewer additionally ran the complete Electron Vitest project:

```text
cd apps/desktop
npm run test:desktop:platforms
```

Result: exit 1, with **192 test files passed / 13 failed / 5 skipped** and **2,405 tests passed / 36 failed / 32 skipped**.

The failures do not execute or implicate the changed resolver. They are attributable to the shared checkout/environment, including:

- `desktop-electron-pin.test.ts`: unrelated `package-lock.json` resolves Electron `43.7.3` while the unchanged Desktop manifest pins `40.10.2`.
- Electron live suites: installed Electron binary is unavailable (`Electron failed to install correctly`).
- Windows-host incompatibilities in POSIX/macOS-oriented tests: `/bin/sh` absent, POSIX mode assertions, Unix path expectations, and symlink privilege failures.

Durable output: `apps/desktop/.scratch/reviewer-t_70a93768/electron-vitest.log`.

This prevents claiming a globally green Electron baseline in the current shared checkout. It does **not** constitute a correctable defect in the scoped version-resolution implementation. QA should preserve this distinction and must not report the entire Electron project as green unless the dependency/environment baseline is first repaired or isolated.

## Findings by Severity

### CRITICAL

None.

### WARNING

1. **Installed runtime verification remains incomplete.** OpenSpec task 4.4 is still unchecked. QA/re-package must verify an installed Windows build with an older `%LOCALAPPDATA%\hermes\hermes-agent` reports the bundled version in both the Remote Mode status bar and About panel, with screenshot or IPC payload evidence.
2. **Global Electron suite is not green in this shared checkout.** The failures are outside this candidate, but downstream reports must not conceal them or state “zero regressions across the full Electron suite” without an isolated green baseline.

### SUGGESTION

1. A future cleanup may remove the four focused ESLint padding warnings. They are non-blocking and should not be mixed into this behavior fix.

## Final Recommendation

Release the pre-created QA task `t_3d52398d`. QA should independently rerun strict validation, Desktop typecheck, focused resolver/status tests, and complete task 4.4 on the packaged Windows artifact. Do not archive the OpenSpec change until that manual runtime evidence is recorded and the downstream QA verdict is complete.
