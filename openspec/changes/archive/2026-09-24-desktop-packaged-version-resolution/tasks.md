# Tasks: Packaged Runtime Version Resolution

## 1. Ladder Context and Ordering (`apps/desktop/electron/runtime-version.ts`)

- [x] 1.1 Add `isPackaged?: boolean` to `VersionResolutionContext` (default `false` when omitted) per design D1
- [x] 1.2 Extract private rung evaluators returning `string | null`: source-tree (`updateRoot/hermes_cli/__init__.py`), install stamp (valid `version` + `shortCommit` → `formatClientVersion`), app version — per design D3
- [x] 1.3 Implement packaged-first ordering when `ctx.isPackaged === true`: stamp → source tree → appVersion → `'0.0.0'`; keep dev ordering (source tree → stamp → appVersion → `'0.0.0'`) otherwise — per design D2 truth table
- [x] 1.4 Update the ladder doc comment to document both orderings and the `isPackaged` switch

## 2. Production Wiring (`apps/desktop/electron/main.ts`)

- [x] 2.1 Pass `isPackaged: IS_PACKAGED` in `resolveHermesVersion()` (`main.ts:17644-17651`), confining the diff to that object literal (reserved hotspot file)
- [x] 2.2 Update the adjacent comment block (`main.ts:17640-17643`) to reflect packaged-first ordering

## 3. Regression Tests (`apps/desktop/electron/runtime-version.test.ts`)

- [x] 3.1 Packaged + valid stamp + local source tree declaring an older `__version__` → resolves to formatted stamp version; assert source file was NOT read (mock call counter)
- [x] 3.2 Packaged + valid dirty stamp → resolves with ` [DIRTY]` suffix
- [x] 3.3 Packaged + no stamp + readable source tree → resolves to source `__version__`
- [x] 3.4 Packaged + stamp with null `version` → falls through to source tree, then appVersion
- [x] 3.5 Packaged + stamp with null `shortCommit` → falls through to source tree, then appVersion
- [x] 3.6 Packaged + no stamp + unreadable source + no appVersion → `'0.0.0'`
- [x] 3.7 `isPackaged: false` with source tree and stamp → source tree wins (dev order unchanged)
- [x] 3.8 `isPackaged` omitted with source tree and stamp → source tree wins (default = dev)
- [x] 3.9 Confirm all pre-existing 14 tests pass without assertion changes

## 4. Verification

- [x] 4.1 `cd apps/desktop && npx vitest run electron/runtime-version.test.ts` → all tests pass; capture output
- [x] 4.2 `cd apps/desktop && npm run typecheck` → exit 0 across all 3 tsconfigs; capture output
- [x] 4.3 `git diff --stat` shows only `runtime-version.ts`, `runtime-version.test.ts`, and `main.ts`; `main.ts` hunk limited to `resolveHermesVersion()` and its comment
- [x] 4.4 Manual runtime check on the affected Windows host: installed build with %LOCALAPPDATA%\hermes\hermes-agent present shows client v0.21.3 (<sha>) in Remote Mode status bar and matching About panel; record screenshot or IPC payload as evidence
- [x] 4.5 `openspec validate desktop-packaged-version-resolution --strict` → exit 0
