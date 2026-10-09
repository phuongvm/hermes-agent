# Exploration Report: Hermes.Crew Desktop Sidebar & Dashboard Integration

**Date**: 2026-10-08  
**Topic**: Integration of Hermes.Crew into Hermes Desktop Sidebar (TOOLS section below OpenSpec) and Hermes Dashboard  
**Target Codebases**: `oss/hermes-agent`, `oss/crew`  

## 1. Architectural Baseline & Existing Seams

### 1.1 Desktop Navigation & Runtime Plugins
- Hermes Desktop features a dynamic runtime plugin mechanism (`apps/desktop/src/contrib/runtime-loader.ts`).
- Electron (`desktop-plugins-root.ts`) mirrors `<HERMES_HOME>/plugins/<package>/desktop/plugin.js` into `<HERMES_HOME>/desktop-plugins/<package>/`.
- The renderer ESM loader imports the module and executes `plugin.register(ctx)`:
  - `ROUTES_AREA`: Mounts full-page workspace route (e.g. `/crew`).
  - `SIDEBAR_NAV_AREA`: Mounts sidebar item with ordering.
- OpenSpec plugin registers `SIDEBAR_NAV_AREA` with `order: 50` and codicon `'list-tree'`.
- Setting Crew's registration to `order: 55` ensures deterministic placement immediately below OpenSpec under the collapsible `TOOLS` group (`SidebarTools`).

### 1.2 Route & Center Area Tab Activation
- `selectSidebarItem` in `apps/desktop/src/app/session/hooks/use-session-actions/index.ts` calls `navigateToWorkspacePage(navigate, item.route)`.
- `navigateToWorkspacePage` runs `navigate(to)` and `revealWorkspacePane()`.
- `syncWorkspaceRoute(path)` switches `$workspaceIsPage` to `true` and commands the workspace pane to front the route, preventing main chat occlusion.
- Right-click split menu (`SplitSubmenu`) invokes `openRouteTile(item.route, dir)`, opening or focusing a parallel route tile.

### 1.3 Hermes Dashboard Web (SPA)
- `web/src/App.tsx` fetches manifests from `/api/dashboard/plugins`.
- `oss/crew/dashboard/manifest.json` specifies:
  - `tab: { path: "/crew", position: "after:openspec" }`.
- `partitionSidebarNav` parses `after:openspec` to position the Crew tab directly below OpenSpec.
- Route `/crew` mounts `<PluginPage name="crew" />`, which renders `dist/index.js` (iframe pointing to `/api/plugins/crew/board`).

## 2. Identified Gap & Action Items
- `oss/crew/desktop/` does not yet exist.
- Need to author `oss/crew/desktop/plugin.js` matching OpenSpec's ESM structure and syncing it to `_config/agent4070/hermes/plugins/crew/desktop/plugin.js`.
- Run tests and typechecks to guarantee zero regression.
