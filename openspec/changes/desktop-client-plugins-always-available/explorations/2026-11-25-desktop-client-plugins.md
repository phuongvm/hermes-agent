# Exploration: Hermes Desktop Client Plugins Always Available

- **Date**: 2026-11-25
- **Change**: `desktop-client-plugins-always-available`
- **Target Repository**: `O:/workspaces/oss/hermes-agent`
- **Source Research**: Swarm board post `#3` (`@researcher`) + Phase 1b bundler/test verification (`@designer`)

---

## 1. Problem Statement

Hermes Desktop users connecting to remote gateways (`hermes-dashboard.inspire-robotics.com`, `asus-vb`, `hermes-vm-02`, or fresh local installations without `~/.hermes/plugins/`) lose access to our 4 core organizational desktop UI plugins — **Crew**, **OpenSpec**, **Radar**, and **Redline** — because Electron's `reconcileUnifiedDesktopHalves()` (`apps/desktop/electron/desktop-plugins-root.ts:577-663`) only copies `plugins/<name>/desktop/plugin.js` from *local* Hermes homes on the same machine. On remote connections (`mode === 'remote'`), Capabilities ▸ Plugins displays `unavailable (remote backend)` for unified packages whose desktop halves are not materialized locally (`plugins-tab.test.tsx:208-227`).

We need all 4 org desktop UI plugins to be **always available** in every Hermes Desktop installation built from our fork (`phuongvm/hermes-agent`), across local, remote, Cloudflare Access/OAuth, profile-switched, and offline states — while keeping local developer hot-reload seamless and preserving upstream sync hygiene.

---

## 2. Architectural Options Evaluated

### Option A (PA 1 — Pure Bundled Build)
Copy `crew`, `openspec`, `radar`, `redline` into `apps/desktop/src/plugins/<name>/plugin.js` so Vite's existing eager glob (`apps/desktop/src/contrib/plugins.ts:21`) compiles them directly into the renderer bundle.

- **Pros**: Zero runtime network dependency; 100% available offline and across remote/OAuth gateways; zero trust-boundary regression; zero changes to `vite.config.ts`, `tsconfig.json`, or `eslint.config.mjs`.
- **Cons (unpatched)**:
  1. Upstream `plugins.ts:50-56` omits `packageName` on bundled records, causing `mergePluginPackages()` (`plugin-packages.ts:85-129`) to split unified packages into **two duplicate rows** (`crew` + `desktop:crew`) whenever the connected backend also reports an `AgentPluginRow`.
  2. Upstream `runtime-loader.ts:447-459` unconditionally shadows any disk copy of a bundled plugin as `${id}:disk-shadowed` (`status: 'disabled'`). On developer machines (`agent4070`, `asus-vb`) where `~/.hermes/plugins/{crew,openspec,radar,redline}` exist locally, Electron's reconcile always mirrors their desktop halves into `desktop-plugins/`, which would spawn 4 bogus `(stale disk copy)` disabled rows AND break linked dev-checkout hot-reload.

### Option B (PA 2 — Remote HTTP Plugin Loading from Gateway)
Serve `/api/desktop-plugins/*` from remote gateways and dynamically `import()` remote ESM inside the Electron renderer.

- **Rejected — 3 Structural Disqualifiers**:
  1. **SurfacePolicy Fail-Closed Gate**: `hermes_cli/web_server_surface.py:59-77, 92-99` sets `SurfacePolicy.desktop_plugins = False` for `serve`, `dashboard`, and `gateway` surfaces (`True` only for `webapp`). Standard gateways return HTTP 404 on `/api/desktop-plugins/*` (`hermes_cli/web_server.py:910`).
  2. **Electron Renderer Trust Boundary (RCE Vector)**: `apps/desktop/src/contrib/runtime-loader.ts:20-36` explicitly documents that runtime plugins evaluate in the Electron renderer realm with full app authority — including `window.hermesDesktop` native file writes, terminal PTY execution, and git mutations (`electron/preload.ts:32-750`). Executing unpinned ESM fetched from a remote gateway violates the local-trust security contract and would require iframe/worker sandboxing + CSP + capability gating.
  3. **Auth/Network/Profile Fragility**: Gateways behind Cloudflare Access or OAuth fail to serve plugin bundles during token refresh, cold boot, or offline startup, stripping the desktop shell of core UI surfaces.

### Option C (Selected — Hybrid PA 1: Bundled Core + Scoped Package Pairing + Controlled Disk Override)
Bundle the 4 org plugins into `apps/desktop/src/plugins/{crew,openspec,radar,redline}/plugin.js` with a committed manifest (`org-plugins.manifest.json`), and add two surgical runtime seams:
1. **Scoped `packageName` pairing** in `plugins.ts` via a dedicated policy module (`bundled-plugin-policy.ts`) so ONLY plugins declaring `packageName` or listed in `org-plugins.manifest.json` pair with `AgentPluginRow` — keeping upstream `hermes-bots`, `kanban`, and `radio` untouched as standalone `kind: 'desktop'` rows.
2. **Controlled 3-way disk collision resolution** in `runtime-loader.ts` + `plugins-store.ts`:
   - Linked dev checkouts (`linked: true` in `.hermes-package.json`) **override** the bundled copy (deactivating bundled first; restoring bundled on retire).
   - Non-linked unified-package copies (`packageName` matches bundled) **override** only when bytes differ (SHA-256 mismatch) AND `sourceMtimeMs > manifest.syncedAtMs`; otherwise **deduplicate silently** without emitting bogus `:disk-shadowed` rows.
   - Standalone/unmatched disk copies keep **exact upstream behavior** (`:disk-shadowed` disabled row), preserving `runtime-loader.test.ts` invariants.

---

## 3. Code-Grounded Verification Findings

### 3.1 Bundler Resolution & SDK Parity (`vite.config.ts` & `src/sdk/`)
Audited all 4 `plugin.js` files from `O:/workspaces/_config/agent4070/hermes/plugins/<name>/desktop/plugin.js` and sibling checkouts under `O:/workspaces/oss/`:

| Plugin | Size (`plugin.js`) | Exported `id` / `name` | Static Import Specifiers | Dynamic Imports | Missing `@hermes/plugin-sdk` Symbols | Extra Desktop Files |
| :--- | ---: | :--- | :--- | :---: | :---: | :--- |
| `crew` | 235,373 B | `"crew"` / `"Crew"` | `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` | None (`0`) | None (`[]`) | `app.js` (818,273 B browser IIFE — **0 references** in `plugin.js`; MUST NOT be bundled) |
| `openspec` | 145,831 B | `'openspec'` / `'OpenSpec'` | `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` | None (`0`) | None (`[]`) | None |
| `radar` | 46,311 B | `'radar'` / `'Radar'` | `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` | None (`0`) | None (`[]`) | None |
| `redline` | 68,271 B | `'redline'` / `'Redline'` | `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` | None (`0`) | None (`[]`) | None |

- `apps/desktop/vite.config.ts:238, 256, 259` already resolves `'@hermes/plugin-sdk'` -> `./src/sdk/index.ts`, `'react'` -> workspace React singleton, and `'react/jsx-runtime'` -> workspace `jsx-runtime.js`. No Vite alias changes are required.
- `apps/desktop/tsconfig.json:7` sets `"allowJs": false` and `apps/desktop/eslint.config.mjs` targets `**/*.{ts,tsx}` — plain-ESM `.js` files in `src/plugins/*/plugin.js` follow the exact same pattern as upstream's `apps/desktop/src/plugins/radio/plugin.js`.
- `scripts/build/freshness.mjs:39-58` hashes all non-generated files under `apps/desktop`, so both Desktop (`buildDesktop`) and Webapp (`buildWebapp`) builds automatically detect changes to `apps/desktop/src/plugins/*/plugin.js`.

### 3.2 Graceful Degradation Without Backend Half
Verified in `#3` across all 4 plugins when connected to a gateway lacking their Python backend half:
- **Crew**: `watchBlocked` wraps polling in `try/catch`; `CrewStatusChip` returns `null`; `Board` renders an inline `"Crew could not load: <error>"` message. Zero toast spam.
- **OpenSpec**: `OpenSpecStatusChip` returns `null` on query failure; `OpenSpecPage` renders `RetryErrorState`. Zero toast spam.
- **Radar**: `watchRadarEvents` catches errors silently; `RadarChip` returns `null`; `RadarPage` renders an inline unavailable banner. Zero toast spam.
- **Redline**: `recentSessionQuery` sets `retry: false`; `Chip` returns `null` at 0 docs; `RedlinePane` renders `.rl-empty`. Zero toast spam.

### 3.3 Existing Unit Test Impact Audit
- `apps/desktop/src/contrib/runtime-loader.test.ts:495-544` (`bundled-shadowed disk copies`): tests standalone disk copy of `hermes-bots` (no `packageName`, no `linkedSource`). Preserved 100% unchanged by the 3-way policy.
- `apps/desktop/src/app/capabilities/plugins/plugin-packages.test.ts:136-147`: asserts bundled `bots` without `packageName` maps to `['desktop:bots', 'desktop']`. Preserved 100% unchanged by scoped `packageName` resolution.
- `apps/desktop/src/app/capabilities/plugins/plugins-tab.test.tsx:542-689, 912-924`: tests bundled `kanban` and `bots` without `packageName`, and tests `disabled` "Install here" when `agentMissingInProfile: true` without `packageOrigin.repo` (lines 325-333). All preserved 100% unchanged.
- `apps/desktop/src/contrib/radio-plugin.test.ts:37, 49, 64, 91, 122, 155`: **CRITICAL FINDING** — this integration test calls `discoverBundledPlugins()` directly and asserts `document.head.querySelectorAll('style').length === initialStyles` after disabling `{ accent: false, kanban: false, 'hermes-bots': false }`. Because `crew`, `openspec`, `radar`, and `redline` ship enabled by default (and inject `<style>` tags on `register()`), `radio-plugin.test.ts` MUST also include `crew: false, openspec: false, radar: false, redline: false` in its test isolation fixture so Radio's style-count assertion stays isolated to Radio.

---

## 4. Decision Summary

Proceed to OpenSpec Proposal (`desktop-client-plugins-always-available`) implementing **Hybrid PA 1** with:
1. Committed `apps/desktop/src/plugins/{crew,openspec,radar,redline}/plugin.js` + `org-plugins.manifest.json`.
2. Deep module `apps/desktop/src/contrib/bundled-plugin-policy.ts` governing scoped `packageName` resolution, synchronous UTF-8 SHA-256 hashing, and 3-way bundled-vs-disk collision decisions.
3. Surgical lifecycle hooks in `plugins-store.ts`, `plugin.ts`, `plugins.ts`, and `runtime-loader.ts`.
4. Deterministic Node ESM sync & verification script `scripts/build/sync-desktop-plugins.mjs` (`sync` + read-only `--check`).
