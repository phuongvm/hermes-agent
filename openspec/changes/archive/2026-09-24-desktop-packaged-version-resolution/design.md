# Design: Packaged Runtime Version Resolution

## Context

`apps/desktop/electron/runtime-version.ts` owns the single authoritative version ladder (`resolveHermesVersionLadder`, lines 174-209) consumed by the `hermes:version` IPC handler, the native About panel, and renderer skew messaging (`main.ts:17644-17701`). The ladder currently has a fixed order:

1. Rung 1 — `ctx.updateRoot/hermes_cli/__init__.py` `__version__` (local Python source tree)
2. Rung 2 — `ctx.installStamp` when `version` and `shortCommit` are both valid → `formatClientVersion(stamp)`
3. Rung 3 — `ctx.appVersion` (`app.getVersion()`), else `'0.0.0'`

The production call site (`main.ts:17644-17651`) always supplies `updateRoot: resolveUpdateRoot()`. `resolveUpdateRoot()` (`main.ts:3092-3100`) returns, in order: `HERMES_DESKTOP_HERMES_ROOT`, `SOURCE_REPO_ROOT` (dev only), then `ACTIVE_HERMES_ROOT` (`HERMES_HOME/hermes-agent`, `main.ts:864`), preferring whichever has a `.git` and otherwise falling back to `ACTIVE_HERMES_ROOT` unconditionally.

Consequence: on a packaged install that coexists with any local Python runtime, Rung 1 always returns that runtime's `__version__`, hiding the packaged client's identity. The renderer then labels it `client v<runtime version>` in Remote Mode (`version-status.ts:83-87`, `i18n/en.ts:3682`).

Verified state (2026-09-24): `hermes_cli/__init__.py` in this checkout declares `0.21.3`; `apps/desktop/package.json` version is `0.17.6`; the existing runtime-version test suite passes 14/14 on baseline.

Constraints from `apps/desktop/AGENTS.md`:
- Electron is the authority for machine and runtime facts; version identity belongs to the main process.
- Tests must not read source files; logic must be pure/DI-testable.
- Keep the seam narrow — do not widen `resolveUpdateRoot()` or the IPC contract.

## Goals / Non-Goals

**Goals:**
- A packaged binary reports the version it was built from whenever its bundled stamp is valid.
- Development runs keep reporting the checkout's `__version__` (existing behavior).
- Fallback chain remains total: a version string is always returned.
- The fix is a pure-function change with explicit input; testable without Electron.

**Non-Goals:**
- Changing how `install-stamp.json` is produced, located, or validated.
- Changing `resolveUpdateRoot()` — it still serves `hermes update`, skew detection, and `hermesRoot` reporting.
- Changing the renderer, i18n copy, or `hermes:version` response shape.
- Reconciling backend (`hermes serve`) version with client version — those are two targets by design (`version-status.ts`).

## Decisions

### D1: Packaging mode is an explicit context input, not inferred
Add `isPackaged?: boolean` to `VersionResolutionContext`. Default `false` when omitted.

Rationale: the ladder is a pure function with DI for `fsModule`; introducing `app.isPackaged` or `process.env` reads inside it would break purity and testability. `IS_PACKAGED` (`main.ts:538`) already encodes `app.isPackaged || Boolean(process.env.HERMES_DESKTOP_IS_PACKAGED)` and is the single truth other installer-only paths use (`main.ts:2441, 4363, 4800, 17699`).

Alternatives considered:
- Infer packaged mode from `stamp.source === 'ci'`: rejected — local `npm run build` stamps carry `source: 'local'` and would be treated as dev; also conflates provenance with process mode.
- Have `main.ts` pass `updateRoot: null` when packaged: rejected — loses the stamp-absent fallback to a local runtime, and hides the decision inside the caller instead of the ladder.

### D2: Truth table for rung order

| `isPackaged` | Order |
|---|---|
| `true` | Stamp (valid version + shortCommit) → Source tree (`updateRoot`) → `appVersion` → `'0.0.0'` |
| `false` / omitted | Source tree (`updateRoot`) → Stamp → `appVersion` → `'0.0.0'` |

Rationale: in a packaged process the bundled stamp is the only artifact that describes the running binary; the source tree describes a sibling runtime. In dev, the checkout is the running code and a stale `build/install-stamp.json` from a prior `npm run build` must not win (existing Rung 1 test at `runtime-version.test.ts:223-250` pins this).

Stamp validity gate is unchanged: `stamp && stamp.version && stamp.shortCommit`. `validateInstallStamp` already nulls malformed fields, so "invalid" collapses to "absent".

### D3: Implementation shape — extract rung evaluators, branch once on mode
Refactor the body of `resolveHermesVersionLadder` into three private evaluators returning `string | null` (`readSourceTreeVersion(ctx, fs)`, `readStampVersion(ctx)`, `readAppVersion(ctx)`), then select the ordered list by `ctx.isPackaged` and return the first non-null, else `'0.0.0'`.

Rationale: avoids duplicating the try/catch and regex; makes the order a data decision that tests can enumerate.

Alternative: keep the linear body and wrap Rung 2 in an early `if (ctx.isPackaged)` block before Rung 1 — acceptable but duplicates the stamp check; rejected for clarity.

### D4: Single production wiring point
`main.ts::resolveHermesVersion()` adds `isPackaged: IS_PACKAGED`. No other call sites exist (`grep resolveHermesVersionLadder` → only `main.ts:401` import and `main.ts:17645` call).

### D5: Regression tests are pure ladder tests
Add cases to `runtime-version.test.ts` under a new describe block covering each spec scenario: packaged+local-runtime, packaged dirty, packaged stamp-absent → source, packaged stamp-invalid (null version / null shortCommit) → source → appVersion, packaged all-absent → `'0.0.0'`, dev order unchanged, omitted flag = dev. Tests must assert `readFileSync` is NOT called on the source path in the packaged-valid-stamp case (mock counter), per spec "the local source tree SHALL NOT be read".

## Risks / Trade-offs

- [Packaged build whose stamp is valid but stale relative to a newer in-place `hermes update` of the local runtime] → This is by design: the client label describes the client binary; the backend target reports its own version separately (`version-status.ts` two-target model). Bundle-skew detection (`detectBundleSkew`) still compares stamp commit vs `resolveUpdateRoot()` tree and surfaces "app build out of date".
- [`HERMES_DESKTOP_IS_PACKAGED` set in a dev shell] → ladder switches to packaged order; if `build/install-stamp.json` exists from a prior build it will be reported. Acceptable and consistent with every other `IS_PACKAGED`-gated path; documented in spec scenario "Packaging flag override for verification".
- [Refactor of ladder body (D3) regresses an existing assertion] → mitigated by the existing 14 tests, which must pass unmodified (spec requirement "Existing ladder and stamp tests remain green").
- [`main.ts` is a reserved hotspot in `agent_share.md` File Ownership] → the change is a one-line addition to an object literal at `main.ts:17645-17650`; the Coder task must confine the diff to that block and flag it in completion metadata.

## Migration Plan

1. Coder implements D1-D5 in a worktree; runs `npx vitest run electron/runtime-version.test.ts` and `npm run typecheck` in `apps/desktop`.
2. Reviewer verifies diff scope (two source files + one test file) and truth-table coverage.
3. Manual verification on the affected Windows machine: launch installed build with `%LOCALAPPDATA%\hermes\hermes-agent` present; status bar in Remote Mode must read `client v0.21.3 (<sha>)`; About panel must match.
4. Rollback: revert the commit; no data migration, no persisted state involved.

## Open Questions

- None blocking. Whether `hermesRoot` in the `hermes:version` response should also reflect "no owning source tree" for packaged builds is out of scope and left as-is.
