# Proposal: Always-Available Bundled Org Desktop Client Plugins (Hybrid PA 1)

## Intent

Ensure our 4 core organizational Hermes Desktop UI plugins — **Crew** (`crew`), **OpenSpec** (`openspec`), **Radar** (`radar`), and **Redline** (`redline`) — are always available out-of-the-box in every Hermes Desktop installation built from our fork (`phuongvm/hermes-agent`), regardless of whether the client connects to a local gateway, a remote gateway (`hermes-dashboard.inspire-robotics.com`, `asus-vb`, `hermes-vm-02`), a Cloudflare Access/OAuth-protected host, a profile without those agent plugins installed, or starts offline.

Simultaneously, preserve clean Capabilities ▸ Plugins package pairing (zero duplicate rows), keep upstream bundled plugins (`hermes-bots`, `kanban`, `radio`) and stale-standalone shadowing semantics untouched, and enable local developer checkouts or newer locally updated unified packages to override bundled copies safely with live hot-reload and automatic fallback on removal.

## Scope

### In Scope
1. **Bundled Org Plugin Assets & Single-Source Manifest**:
   - Commit the plain-ESM `plugin.js` entrypoints for `crew`, `openspec`, `radar`, and `redline` under `apps/desktop/src/plugins/<id>/plugin.js` (explicitly excluding `crew/desktop/app.js`, which is an unused 818 KB standalone browser IIFE).
   - Commit `apps/desktop/src/plugins/org-plugins.manifest.json` recording each bundled org plugin's canonical `id`, `packageName`, `sha256`, `syncedAtMs`, and `sourceHint`.
2. **Scoped Package Pairing (`plugins.ts`, `plugin.ts`, `bundled-plugin-policy.ts`)**:
   - Extend `HermesPlugin` with optional `readonly packageName?: string`.
   - Resolve `record.packageName` in `discoverBundledPlugins()` strictly for plugins that explicitly declare `packageName` or match an entry in `org-plugins.manifest.json`, so `mergePluginPackages()` merges them with backend `AgentPluginRow` entries into a single `kind: 'both'` row while leaving `hermes-bots`, `kanban`, and `radio` unaffected as `kind: 'desktop'`.
3. **Controlled Disk-Override & Deduplication (`bundled-plugin-policy.ts`, `plugins-store.ts`, `runtime-loader.ts`)**:
   - Preserve `sourceMtimeMs` and `linkedSource` from `.hermes-package.json` in `readFolderIdentity()`.
   - Introduce `suspendBundledPlugin(id)` and `restoreBundledPlugin(id)` in `plugins-store.ts`.
   - Evaluate bundled-vs-disk collisions through a pure 3-way policy (`'override' | 'dedup-bundled' | 'shadow-stale'`):
     - **Linked dev checkout** (`linked: true` / `linkedSource` set): suspend bundled, load and hot-reload disk copy, restore bundled when disk folder retires.
     - **Non-linked unified package copy** (`packageName` matches bundled): override bundled only when bytes differ (`sha256` mismatch) AND `sourceMtimeMs > syncedAtMs`; otherwise silently deduplicate (keep bundled active without publishing a bogus `:disk-shadowed` row).
     - **Standalone / unmatched disk copy**: retain exact upstream behavior (skip disk copy, publish `${id}:disk-shadowed` disabled row).
4. **Deterministic Sync & Verification Script (`scripts/build/sync-desktop-plugins.mjs`)**:
   - Add a zero-dependency Node ESM script with two modes:
     - Default sync mode: resolves canonical `plugin.js` sources from sibling workspace checkouts (`../crew`, `../hermes-openspec`, `../hermes-redline`, or `${HERMES_HOME}/plugins/<name>` fallback), validates import allowlist (`@hermes/plugin-sdk`, `react`, `react/jsx-runtime`, `react/jsx-dev-runtime`), copies exact bytes, and updates `org-plugins.manifest.json`.
     - `--check` verification mode (100% read-only & offline): validates all 4 bundled `plugin.js` files exist, match manifest SHA-256 digests, pass import allowlist checks, and contain no prohibited extra files (such as `crew/app.js`).
   - Expose `"sync:plugins"` and `"check:plugins"` scripts in `apps/desktop/package.json`.
5. **Unit & Integration Test Coverage**:
   - Pure unit tests for `bundled-plugin-policy.ts` and `sync-desktop-plugins.mjs`.
   - Lifecycle tests for `discoverBundledPlugins()`, `plugins-store.ts` suspend/restore, `runtime-loader.ts` 3-way collision resolution, and `mergePluginPackages()` pairing.
   - Update `apps/desktop/src/contrib/radio-plugin.test.ts` test isolation fixture so the 4 new default-enabled bundled plugins do not pollute Radio's `document.head` style-count assertion.

### Non-Goals
- **No Remote HTTP Plugin Fetching (PA 2)**: Do NOT expose `/api/desktop-plugins/*` on `gateway`/`dashboard`/`serve` surfaces, do NOT modify `hermes_cli/web_server_surface.py`, and do NOT evaluate remote ESM over HTTP in the Electron renderer.
- **No Changes to Upstream Plugin Source Logic**: Do NOT fork or hand-patch the contents of `crew`, `openspec`, `radar`, or `redline` `plugin.js` files inside `hermes-agent`; synced copies remain byte-identical to their source repos.
- **No Electron Main/Preload Changes**: Do NOT modify `apps/desktop/electron/**` or `desktop-plugins-root.ts` (Electron already writes `package`, `source`, `sourceMtimeMs`, and `linked` into `.hermes-package.json`).
- **No Installer / Lockfile / Dependency Mutations**: Do NOT add npm dependencies, alter `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `vite.config.ts`, or `apps/desktop/scripts/build.mjs` build-time checkout cleanliness.

## Upstream Sync Invariant Impact (INV-1..INV-9)

Audited against `hermes-upstream-sync/SKILL.md` v2.3.0 (9 core invariants; note no INV-10 is currently defined in the matrix):

| Invariant | Affected? | Impact Analysis |
| :--- | :---: | :--- |
| **INV-1** (`acp_adapter/entry.py`, `session.py`) | No | Zero files touched. |
| **INV-2** (`plugins/platforms/buzz/adapter.py`) | No | Zero files touched. |
| **INV-3** (`apps/desktop/electron/main.ts`, `backend-health.ts`) | No | Zero files touched in Electron main process or auth latch. |
| **INV-4** (`@assistant-ui/tap: 0.9.8` pin, `incremental-external-store-runtime.ts`) | No | Zero dependency or runtime store changes. |
| **INV-5** (`hermes_cli/gateway.py`) | No | Zero files touched. |
| **INV-6** (`hermes_cli/web_routers/profiles.py`, `web_read_coalescing.py`) | No | Zero files touched. |
| **INV-7** (`apps/shared/src/reconnect-backoff.ts`, `tools/self_repo_guard.py`) | No | Zero files touched. |
| **INV-8** (`electron-builder.config.cjs`, `run-electron-builder.mjs`, `build.mjs`) | No | Zero packaging script edits; Vite's existing glob compiles `src/plugins/*/plugin.js` directly into `dist/` with no dirty-tree writes during `pnpm run build`. |
| **INV-9** (`apps/desktop/package.json` Electron `44.10.2` 3-surface pin & lockfile) | Preserved | Only adds two non-dependency `"scripts"` entries (`sync:plugins`, `check:plugins`) to `apps/desktop/package.json`; `dependencies`, `devDependencies`, `pnpm-workspace.yaml`, and `pnpm-lock.yaml` remain byte-unchanged. |

**Future Upstream Merge Footprint**: All 4 plugin bundles, `org-plugins.manifest.json`, `bundled-plugin-policy.ts`, and `scripts/build/sync-desktop-plugins.mjs` are net-new files with zero conflict surface. Existing-file edits are confined to small, localized hunks in `plugin.ts`, `plugins.ts`, `plugins-store.ts`, `runtime-loader.ts`, `radio-plugin.test.ts`, and `apps/desktop/package.json` scripts.

## Rollout Plan (Fork Installer & Fleet)

1. Implement and verify the OpenSpec tasks inside an isolated git worktree (`O:/workspaces/.worktrees/wt-desktop-client-plugins`) per `§9 AGENTS.md`.
2. Run `node scripts/build/sync-desktop-plugins.mjs --check`, `pnpm --filter hermes run typecheck`, `pnpm --filter hermes run lint`, `pnpm --filter hermes run test:ui`, and `pnpm --filter hermes run build` inside the worktree.
3. Pass independent two-axis review (`@reviewer`) and verification (`@qa`).
4. Push verified branch from worktree to `origin/main` after Commander authorization; build the Windows installer (`Hermes-<version>-win-x64.exe`) and publish via the existing release pipeline so all client installations (`agent4070`, `asus-vb`, remote Windows clients) receive bundled `crew`, `openspec`, `radar`, and `redline` out-of-the-box.
5. Future plugin updates follow a 2-step workflow: update the plugin repo -> run `pnpm --filter hermes run sync:plugins` -> commit the refreshed `plugin.js` + `org-plugins.manifest.json` -> build & ship.
