# Change Proposal: Crew Dashboard UI & Theme Enhancements

## Why
The integration of Crew coordination dashboard into Hermes Desktop provides real-time visibility into multi-agent task execution. However, user testing revealed four usability and visual defects:
1. Active running tasks do not display the radar gradient sweep animation seen in Hermes Dashboard.
2. The Crew dashboard retains a dark background regardless of whether Hermes Desktop is set to a light or dark theme.
3. The 'Clear All' notification button fails silently due to loopback origin/proxy checks, leaving notifications flooding the UI.
4. The dashboard header hardcodes 'crew board' instead of displaying the active Kanban board name.

Resolving these issues ensures seamless theme continuity, responsive interaction feedback, and accurate board identity across Hermes Desktop and Web Dashboard.

## What Changes
1. **Radar Sweep Animation for Running Tasks**:
   - Update `oss/crew/scripts/crew_dashboard/board.js` to ensure tasks with `status === 'running'` receive active motion classes.
   - Refine `crew.css` conic-gradient radar sweep styling to match the proven `.kanban-arc` animation in Hermes Desktop.
2. **Theme Synchronization**:
   - Remove hardcoded `#041c1c !important` theme overrides from `oss/crew/dashboard/plugin_api.py`.
   - Update `oss/crew/desktop/plugin.js` to pass active theme tokens (`--dt-background`, `--dt-card`, `--ui-text-primary`, `--ui-stroke-tertiary`, `color-scheme`) via query params and `postMessage`.
   - Enable `tokens.css` and `board.js` to ingest dynamic theme tokens and adapt the color-scheme seamlessly.
3. **Notification 'Clear All' & ACK Authorization**:
   - Relax `_same_origin()` in `oss/crew/scripts/crew_graph_serve.py` to allow loopback proxy requests from local desktop and gateway ports.
   - Ensure `board.js` handles ACK responses robustly and clears notification rows immediately on success.
4. **Dynamic Kanban Board Title**:
   - Resolve the active board slug in `crew_graph.py` and include `"board": "<slug>"` in `/board.json`.
   - Dynamically render the board name in `<h1>` of `board_page()` and in Hermes Desktop header `<span>`.
5. **Runtime Parity Sync**:
   - Synchronize all updated files from `oss/crew/` to `_config/agent4070/hermes/plugins/crew/`.

## Capabilities

### Modified Capabilities
- `crew-dashboard-ui`: Animation, theming, notifications, and board title resolution across Crew dashboard and Hermes Desktop integration.
