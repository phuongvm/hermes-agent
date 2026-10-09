# Proposal: Crew Desktop Dedicated Tab & Tile Routing

## 1. Executive Summary
Provide a first-class declarative mechanism for desktop sidebar navigation items to open as a dedicated, switchable tab in the center workspace area (`PaneTabStrip`) rather than taking over the entire workspace view.

## 2. Motivation
Currently, clicking the "Crew" item under the TOOLS sidebar navigation replaces the workspace view with a full-page route and hides the top tab strip. Users chatting with agents cannot switch between Chat and Crew without repeatedly expanding the TOOLS group in the sidebar. Enabling Crew to open as a dedicated center area tab provides instant 1-click tab switching and preserves multi-pane split capabilities.

## 3. Scope of Changes
- `oss/hermes-agent/apps/desktop/src/app/routes.ts`: Add `asTile?: boolean` to `SidebarNavContribution`.
- `oss/hermes-agent/apps/desktop/src/app/types.ts`: Add `asTile?: boolean` to `SidebarNavItem`.
- `oss/hermes-agent/apps/desktop/src/store/route-tiles.ts`: Support `dir: TileDock = 'center'` and focus via `revealTreePane`.
- `oss/hermes-agent/apps/desktop/src/app/chat/sidebar/index.tsx`: Map `asTile` from contribution into `SidebarNavItem`.
- `oss/hermes-agent/apps/desktop/src/app/session/hooks/use-session-actions/index.ts`: In `selectSidebarItem`, open and focus route tile when `item.asTile` is set.
- `oss/crew/desktop/plugin.js`: Declare `asTile: true` on the `SIDEBAR_NAV_AREA` contribution and sync to runtime copies.

## 4. Verification Criteria
- Unit tests verify `SidebarTools` and `selectSidebarItem` route tile activation and focus behavior.
- Vitest suite `navigation.test.tsx` and route-tiles test pass 100%.
- Typecheck clean across all 4 tsconfig targets.
- Strict OpenSpec validation passes with exit code 0.
