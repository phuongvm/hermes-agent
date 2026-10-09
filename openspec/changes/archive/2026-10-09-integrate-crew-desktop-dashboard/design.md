# Design: Crew Desktop & Dashboard Integration

## 1. Architectural Authority
- Hermes Desktop: `apps/desktop/AGENTS.md`, `apps/desktop/DESIGN.md`, `apps/desktop/ENGINEERING.md`.
- Runtime Plugin System: `apps/desktop/src/contrib/runtime-loader.ts`.
- Crew Plugin Architecture: `oss/crew/plugin.yaml`, `oss/crew/dashboard/manifest.json`.

## 2. Desktop Plugin Extension Point
Hermes Desktop uses a dynamic runtime plugin mechanism:
- Electron scans `<HERMES_HOME>/plugins/<name>/desktop/plugin.js` and mirrors it into `<HERMES_HOME>/desktop-plugins/<name>/`.
- The renderer ESM loader (`runtime-loader.ts`) imports the module and executes `plugin.register(ctx)`.
- OpenSpec registers:
  ```js
  ctx.registerMany([
    { id: 'page', area: ROUTES_AREA, data: { path: '/openspec' }, render: ... },
    { id: 'nav', area: SIDEBAR_NAV_AREA, order: 50, data: { codicon: 'list-tree', label: 'OpenSpec', path: '/openspec' } }
  ]);
  ```
- Crew Desktop plugin (`oss/crew/desktop/plugin.js`) will register:
  ```js
  ctx.registerMany([
    { id: 'page', area: ROUTES_AREA, data: { path: '/crew' }, render: () => jsx(CrewPage, {}) },
    { id: 'nav', area: SIDEBAR_NAV_AREA, order: 55, data: { codicon: 'organization', label: 'Crew', path: '/crew' } }
  ]);
  ```
  Setting `order: 55` ensures it is deterministically placed directly below OpenSpec (`order: 50`).

## 3. Center-Area Tab & Navigation Behavior
- In Desktop, `selectSidebarItem` calls `navigateToWorkspacePage(navigate, item.route)`.
- `navigateToWorkspacePage` invokes `navigate(to)` and `revealWorkspacePane()`.
- `syncWorkspaceRoute('/crew')` sets `$workspaceIsPage.set(true)`, which commands the workspace pane to front the `/crew` route component.
- Context menu split: Right-clicking the item invokes `SplitSubmenu` -> `openRouteTile('/crew', dir)`, opening or focusing the tile beside main chat.
- In Web Dashboard (`web/src/App.tsx`), `manifest.json` already specifies `position: "after:openspec"`, mapping directly into `partitionSidebarNav` and rendering `/crew` route.

## 4. Negative Impact Analysis
- **Side-effect 1**: Syntax or import error in `desktop/plugin.js` could cause plugin load failure.
  *Remediation*: Confine imports strictly to `@hermes/plugin-sdk` and `react/jsx-runtime`; validate syntax before deployment.
- **Side-effect 2**: Iframe theme mismatch or broken upstream proxy.
  *Remediation*: Inject CSS theme sync matching Hermes Desktop CSS variables (`--ui-panel-background`, `--foreground`).
- **Side-effect 3**: Tab collision with existing routes.
  *Remediation*: Route `/crew` is non-reserved and cleanly treated as `isContributedPath('/crew')`.
