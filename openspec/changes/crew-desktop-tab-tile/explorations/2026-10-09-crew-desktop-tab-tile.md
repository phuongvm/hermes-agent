# Exploration: Crew Desktop Separated Tab & Route Tile Architecture

- **Date**: 2026-10-09
- **Topic**: Opening Crew as a Dedicated Tab in Hermes Desktop Center Area
- **Target Repository**: `oss/hermes-agent` and `oss/crew`

## 1. Problem Definition
Currently, when a user clicks the "Crew" item under the TOOLS group in the left sidebar of Hermes Desktop, `selectSidebarItem` routes directly to `/crew` using full-page workspace navigation (`navigateToWorkspacePage`).
This replaces the active workspace view (e.g. Chat session) with the full-page Crew view, sets `$workspaceIsPage = true`, and enables `headerVeto` which completely hides the top tab strip.
As a result:
- There is no tab for Crew in the center area.
- The user loses visual continuity with their chat session.
- To switch back to chat and then return to Crew, the user must repeatedly expand the TOOLS group in the sidebar and click Crew.

## 2. Architectural Analysis
Hermes Desktop already has a robust layout tree architecture supporting multiple types of tabs in the center area:
- `workspace`: Primary session thread view.
- `session-tile:<id>`: Secondary session tabs.
- `route-tile:<path>`: Full-page route views docked as layout panes/tabs (`src/store/route-tiles.ts` & `src/app/chat/route-tile.tsx`).
- `preview-tile:<url>`: Preview panes.

When a pane is docked into the active tree group with `pos: 'center'`, it renders as a native tab on the `PaneTabStrip`.
Furthermore, calling `revealTreePane('route-tile:' + path)` automatically focuses/activates that tab.

## 3. Clean Declarative Extension Design
To preserve core integrity and avoid hardcoding plugin-specific paths (`/crew`) in core navigation hooks:
1. **Plugin SDK Declaration**:
   - In `oss/hermes-agent/apps/desktop/src/app/routes.ts`, extend `SidebarNavContribution` with optional field:
     `asTile?: boolean`
   - In `oss/hermes-agent/apps/desktop/src/app/types.ts`, mirror `asTile?: boolean` on `SidebarNavItem`.
2. **Core Navigation Wiring**:
   - In `oss/hermes-agent/apps/desktop/src/app/chat/sidebar/index.tsx`, propagate `asTile: data.asTile` into `contributedNav`.
   - In `oss/hermes-agent/apps/desktop/src/app/session/hooks/use-session-actions/index.ts`, update `selectSidebarItem`:
     When `item.asTile && item.route`, invoke `openRouteTile(item.route, 'center')` and `revealTreePane('route-tile:' + item.route)`.
3. **Route Tile Center Docking Support**:
   - In `oss/hermes-agent/apps/desktop/src/store/route-tiles.ts`, update `openRouteTile(path: string, dir: TileDock = 'center')` to support `'center'` docking and automatically trigger `revealTreePane('route-tile:' + path)`.
4. **Crew Plugin Declaration**:
   - In `oss/crew/desktop/plugin.js`, add `asTile: true` to the `SIDEBAR_NAV_AREA` contribution.
