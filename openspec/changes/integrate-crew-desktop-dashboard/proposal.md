## Why

Hermes.Crew (v0.7.9) is a specialized autonomous multi-agent coordination system with its own proof-gated board and live flow graph server (`crew_graph_serve` on port 8799). While the backend proxy and dashboard manifest exist, Crew is not yet integrated into the Hermes Desktop left sidebar (under the TOOLS group, below OpenSpec) or seamlessly routable as a dedicated center-area tab on both Desktop and Web Dashboard. Integrating Crew gives developers and operators immediate, 1-click access to the Crew coordination board alongside existing OpenSpec and chat workflows.

## What Changes

1. **Hermes Desktop Left Sidebar Integration**:
   - Add a 'Crew' navigation entry into the left sidebar within the collapsible `TOOLS` section, positioned immediately below the `OpenSpec` item.
   - Implement Crew desktop plugin (`oss/crew/desktop/plugin.js`) registering `SIDEBAR_NAV_AREA` with `order: 55` (OpenSpec is `order: 50`) and icon `'organization'`.
   - Register route `/crew` in `ROUTES_AREA` mounting the Crew board.

2. **Center-Area Tab & Navigation Activation**:
   - On click of 'Crew' in Desktop sidebar, route to `/crew` and reveal the center workspace pane (`revealWorkspacePane()`).
   - If the Crew tab/tile is already open (e.g. as a route tile beside the main chat), focus/activate that tab rather than creating duplicates.
   - Support right-click "Open in split" for parallel pane viewing beside live chat.

3. **Hermes Web Dashboard Parity**:
   - Ensure the Crew tab (`/crew`) in the Web Dashboard (`web/src/App.tsx`) renders under the tools navigation with position `after:openspec`.
   - Ensure clicking the item activates the tab in the main area and focuses the iframe view.

4. **Synchronize Plugin Artifacts**:
   - Deploy/sync `desktop/plugin.js` from `oss/crew/desktop/` to `_config/agent4070/hermes/plugins/crew/desktop/`.

## Capabilities

### Modified Capabilities
- `desktop-sidebar-navigation`: Add Crew navigation link under TOOLS section below OpenSpec, and support center-area tab activation.
