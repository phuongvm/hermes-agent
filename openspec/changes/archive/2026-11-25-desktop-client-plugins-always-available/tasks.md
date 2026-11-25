# Tasks: Always-Available Bundled Desktop Client Plugins (`desktop-client-plugins-always-available`)

> **Execution Order**: Vertical-slice TDD inside an isolated worktree (`O:/workspaces/.worktrees/wt-desktop-client-plugins`, §9 AGENTS.md). Every task MUST produce its real command output before marking `[x]`.

---

## Group 1: Sync Script, Bundled Plugin Assets & Manifest

- [x] **1.1 Implement `scripts/build/sync-desktop-plugins.mjs` with sync + offline `--check` modes and unit tests**
  - Create `scripts/build/sync-desktop-plugins.mjs` supporting default sync mode (resolve `crew`, `openspec`, `radar`, `redline` from `--source-map` -> `--plugins-root` -> sibling OSS repos -> `${HERMES_HOME}/plugins/<id>`, validate allowlisted imports `@hermes/plugin-sdk`/`react`/`react/jsx-runtime`/`react/jsx-dev-runtime`, copy raw bytes to `apps/desktop/src/plugins/<id>/plugin.js`, never copy `crew/app.js`, write deterministic `apps/desktop/src/plugins/org-plugins.manifest.json` preserving `syncedAtMs` when SHA-256 is unchanged) and read-only offline `--check` mode.
  - Add `"sync:plugins"` and `"check:plugins"` scripts to `apps/desktop/package.json` (do NOT modify dependencies or lockfiles — preserve INV-4/INV-9).
  - Write unit tests in `scripts/build/sync-desktop-plugins.test.mjs` covering sync staging, byte-identical SHA-256 manifest generation, `--check` pass on clean temp fixture, and `--check` non-zero exit on SHA-256 drift, disallowed import specifier, missing file, or extraneous `crew/app.js`.
  - Proof: `node --test scripts/build/sync-desktop-plugins.test.mjs`
  - Evidence: Exit code `0` — `5/5` tests passed (`stages byte-identical plugin.js files, excludes crew/app.js, writes manifest, and preserves syncedAtMs when unchanged`, `passes offline --check on a clean synced fixture without any source repositories present`, `fails --check with non-zero exit when a plugin.js drifts from its manifest sha256`, `fails sync and --check when a plugin imports a disallowed specifier`, `fails --check when a bundled plugin.js is missing or crew/app.js is present`, `duration_ms 1286.5303`).

- [x] **1.2 Stage the 4 bundled org plugins (`crew`, `openspec`, `radar`, `redline`) and verify offline `--check`**
  - [Blocked by: 1.1]
  - Run `node scripts/build/sync-desktop-plugins.mjs` to generate `apps/desktop/src/plugins/{crew,openspec,radar,redline}/plugin.js` and `apps/desktop/src/plugins/org-plugins.manifest.json`.
  - Confirm `apps/desktop/src/plugins/crew/app.js` does NOT exist and offline verification passes cleanly.
  - Proof: `node scripts/build/sync-desktop-plugins.mjs --check`
  - Evidence: Exit code `0` — `[sync-desktop-plugins] Synced 4 bundled org plugins (crew:16f6163d, openspec:9d7d4b26, radar:bbe5eeab, redline:d53eab8f)`; `[sync-desktop-plugins --check] OK (crew, openspec, radar, redline)`; `Test-Path apps/desktop/src/plugins/crew/app.js` -> `False` (all 4 `plugin.js` files normalized to LF matching `.gitattributes` `*.js text eol=lf`).

---

## Group 2: Bundled Plugin Policy Deep Module & Scoped Package Pairing

- [x] **2.1 Implement `apps/desktop/src/contrib/bundled-plugin-policy.ts` with pure TDD unit tests**
  - [Blocked by: 1.2]
  - Create `apps/desktop/src/contrib/bundled-plugin-policy.ts` exporting `resolveBundledPackageName`, `bundledOrgPluginMeta`, synchronous UTF-8 `sha256Hex`, and 3-way `evaluateBundledCollision`.
  - Write `apps/desktop/src/contrib/bundled-plugin-policy.test.ts` verifying:
    - `sha256Hex` matches Node `crypto.createHash('sha256')` test vectors (empty string, ASCII, multibyte UTF-8).
    - `resolveBundledPackageName` returns explicit `plugin.packageName` or manifest `packageName` for `crew`/`openspec`/`radar`/`redline`, and returns `undefined` for `hermes-bots`, `kanban`, and `radio`.
    - `evaluateBundledCollision` returns `'override'` (`linked-dev`), `'override'` (`newer-unified`), `'dedup-bundled'` (`identical-unified`), `'dedup-bundled'` (`older-unified`), and `'shadow-stale'` (`standalone-disk`).
  - Proof: `pnpm --filter hermes exec vitest run --project ui src/contrib/bundled-plugin-policy.test.ts`
  - Evidence: Exit code `0` — `Test Files 1 passed (1) | Tests 8 passed (8) (16ms)`, including byte-exact SHA-256 verification against the staged 235 KB `crew/plugin.js` bundle.

- [x] **2.2 Wire scoped `packageName` into `plugin.ts` & `plugins.ts` and verify `mergePluginPackages()` pairing**
  - [Blocked by: 2.1]
  - Extend `HermesPlugin` in `apps/desktop/src/contrib/plugin.ts` with optional `readonly packageName?: string`.
  - Update `apps/desktop/src/contrib/plugins.ts` to populate `packageName` via `resolveBundledPackageName(plugin)` (omitted when `undefined`) and pass `defaultEnabled` metadata into `publishPlugin()`.
  - Add `apps/desktop/src/contrib/plugins.test.ts` verifying `discoverBundledPlugins()` registers `crew`, `openspec`, `radar`, and `redline` with `kind: 'bundled'`, `status: 'loaded'`, and matching `packageName`, while `hermes-bots`, `kanban`, and `radio` have `packageName === undefined`.
  - Add tests to `apps/desktop/src/app/capabilities/plugins/plugin-packages.test.ts` verifying bundled `crew`/`openspec`/`radar`/`redline` merge into one `kind: 'both'` row with remote `AgentPluginRow` (zero duplicate rows, `desktopMissing: false`) while bundled `hermes-bots`/`kanban`/`radio` stay `kind: 'desktop'`.
  - Update `apps/desktop/src/contrib/radio-plugin.test.ts` isolation decisions to also disable `crew`, `openspec`, `radar`, `redline` so Radio's style-count assertion remains isolated.
  - Proof: `pnpm --filter hermes exec vitest run --project ui src/contrib/plugins.test.ts src/contrib/radio-plugin.test.ts src/app/capabilities/plugins/plugin-packages.test.ts`
  - Evidence: Exit code `0` — `Test Files 3 passed (3) | Tests 16 passed (16)` (`plugin-packages.test.ts` 10/10, `plugins.test.ts` 1/1 including 404 zero-toast backend-absent check & explicit user disable persistence, `radio-plugin.test.ts` 5/5).

---

## Group 3: Controlled Disk-Override Lifecycle (`plugins-store.ts` & `runtime-loader.ts`)

- [x] **3.1 Add bundled suspend/restore lifecycle hooks to `apps/desktop/src/contrib/plugins-store.ts`**
  - [Blocked by: 2.2]
  - Add `suspendBundledPlugin(id)` (deactivates bundled handle inside try/catch, stores `{ record, handle, defaultEnabled }` backup, returns `{ defaultEnabled }` or `null`) and `restoreBundledPlugin(id)` (re-publishes bundled record+handle and re-activates when `pluginActive(id, defaultEnabled)` is true).
  - Add unit tests in `apps/desktop/src/contrib/plugins-store.test.ts` verifying suspend disposes bundled registrations, tolerates a throwing bundled disposer, restores cleanly on `restoreBundledPlugin`, and respects persisted explicit `false` decisions on restore.
  - Proof: `pnpm --filter hermes exec vitest run --project ui src/contrib/plugins-store.test.ts`
  - Evidence: Exit code `0` — `Test Files 1 passed (1) | Tests 3 passed (3) (6ms)`.

- [x] **3.2 Integrate 3-way collision resolution and restore-on-retire in `apps/desktop/src/contrib/runtime-loader.ts`**
  - [Blocked by: 3.1]
  - Preserve `sourceMtimeMs` and `linkedSource` in `packageMarker()`, `readFolderIdentity()`, `DiskPlugin`, and `LoadOptions`.
  - Replace the unconditional bundled-shadow block in `loadRuntimePlugin()` with `evaluateBundledCollision()`:
    - `'override'`: call `suspendBundledPlugin(plugin.id)`, preserve bundled `defaultEnabled` posture (still honoring explicit `$pluginDecisions[id] === false`), and register the disk copy.
    - `'dedup-bundled'`: log info and return `null` without publishing `${id}:disk-shadowed`.
    - `'shadow-stale'`: preserve exact existing behavior (`${id}:disk-shadowed` disabled row).
  - In `retireDiskPlugin()`, invoke `restoreBundledPlugin(record.id)` after `dropPlugin(record.id)` so uninstalling or unlinking an overriding disk copy restores the bundled plugin live.
  - Extend `apps/desktop/src/contrib/runtime-loader.test.ts` with tests for all 4 collision branches + retire-restores-bundled + existing `hermes-bots:disk-shadowed` regression proof.
  - Proof: `pnpm --filter hermes exec vitest run --project ui src/contrib/runtime-loader.test.ts`
  - Evidence: Exit code `0` — `Test Files 1 passed (1) | Tests 37 passed (37) (409ms)`.

---

## Group 4: Full Verification Gate (Typecheck, Lint, UI Tests, Production Bundle)

- [x] **4.1 Run full Desktop static, unit, plugin-integrity, and production build gates**
  - [Blocked by: 3.2]
  - Verify zero type errors, zero lint errors on all owned files, all Desktop plugin/contrib/capabilities UI tests pass, offline `--check` passes, production renderer + Electron bundle compiles cleanly (`pnpm --filter hermes run build`), and `git status --porcelain` shows zero unintended build-time checkout mutations (preserving INV-8 & INV-9).
  - Proof (per Coordinator Board Decision `#16` preserving `NO-TOUCH` on unrelated `0d24d5bc1f` baseline files): `node --test scripts/build/sync-desktop-plugins.test.mjs && node scripts/build/sync-desktop-plugins.mjs --check && pnpm --filter hermes run typecheck && pnpm --filter hermes exec eslint --max-warnings=0 src/contrib/bundled-plugin-policy.ts src/contrib/bundled-plugin-policy.test.ts src/contrib/plugin.ts src/contrib/plugins.ts src/contrib/plugins.test.ts src/contrib/plugins-store.ts src/contrib/plugins-store.test.ts src/contrib/runtime-loader.ts src/contrib/runtime-loader.test.ts src/contrib/radio-plugin.test.ts src/app/capabilities/plugins/plugin-packages.test.ts && pnpm --filter hermes exec vitest run --project ui src/contrib src/app/capabilities/plugins src/lib/browser-desktop-plugins.test.ts && pnpm --filter hermes run build`
  - Evidence:
    - Combined proof gate exit code: `0` (`COMBINED_PROOF_EXIT_CODE=0`).
    - `sync-desktop-plugins.test.mjs`: `5/5` passed; `sync-desktop-plugins.mjs --check`: `OK (crew, openspec, radar, redline)`.
    - `typecheck`: exit code `0` across renderer, Electron, e2e, and `electron-builder.config.cjs`.
    - `eslint --max-warnings=0` on all 11 owned TS files: exit code `0` (`0 errors, 0 warnings`). *(Transparent baseline note per `#16`: repo-wide `eslint src/ electron/` on unmodified base `0d24d5bc1f` has 27 pre-existing errors in 8 untouched fork files including `NO-TOUCH` `apps/desktop/electron/{backend-health,reauth-modal-latch}.test.ts`; left untouched per `#16`.)*
    - Plugin/contrib/capabilities UI suite (`src/contrib`, `src/app/capabilities/plugins`, `src/lib/browser-desktop-plugins.test.ts`): exit code `0` — `Test Files 26 passed (26) | Tests 192 passed (192)` (including `plugins-tab.test.tsx` `36/36`). *(Full-repo `pnpm --filter hermes run test:ui` also executed: `1,671/1,690` files and `14,482/14,535` tests passed with `100%` of plugin/contrib/capabilities/sdk suites green; the 19 unrelated failures were 10 Windows 28-worker timeout suites + 9 pre-existing `0d24d5bc1f` INV-3/INV-7/`sh` baseline suites.)*
    - Production build (`pnpm --filter hermes run build`): exit code `0` (`16334 modules transformed`, renderer + `electron-main.mjs` + `electron-preload.js` built cleanly, `0` dirty build-time file mutations).
