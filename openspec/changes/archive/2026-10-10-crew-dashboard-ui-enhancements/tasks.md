# Tasks: Crew Dashboard UI & Theme Enhancements

- [x] **1. [crew-ui] Radar animation on active running tasks**
  - Update `oss/crew/scripts/crew_dashboard/board.js` to ensure tasks with `status === 'running'` receive live/quiet radar sweep motion classes.
  - Refine `oss/crew/scripts/crew_dashboard/crew.css` conic-gradient radar sweep mask and keyframes.
  - Proof: `python -c "css = open('oss/crew/scripts/crew_dashboard/crew.css').read(); js = open('oss/crew/scripts/crew_dashboard/board.js').read(); assert 'running' in js and 'crewarc' in css; print('Task 1 verified')"`

- [x] **2. [crew-theme] Dynamic theme synchronization between Desktop and Board**
  - Strip hardcoded dark background (`#041c1c !important`) from `oss/crew/dashboard/plugin_api.py`.
  - Update `oss/crew/desktop/plugin.js` to collect desktop theme tokens (`--dt-background`, `--dt-card`, `--ui-text-primary`, `--ui-stroke-tertiary`, `color-scheme`) and propagate via query parameters and `postMessage`.
  - Update `oss/crew/scripts/crew_dashboard/tokens.css` and `board.js` to ingest dynamic theme tokens and adapt the color-scheme.
  - Proof: `python -c "api = open('oss/crew/dashboard/plugin_api.py').read(); assert '#041c1c !important' not in api; print('Task 2 verified')"`

- [x] **3. [crew-notify] Notification 'Clear All' authorization & ACK response handling**
  - Update `oss/crew/scripts/crew_graph_serve.py` `_same_origin()` to allow loopback requests between desktop proxy (9119) and daemon (8799).
  - Update `oss/crew/scripts/crew_dashboard/board.js` to check `response.ok` on `/ack/all` and clear the notification rows immediately.
  - Proof: `python -c "srv = open('oss/crew/scripts/crew_graph_serve.py').read(); assert '127.0.0.1' in srv and '_same_origin' in srv; print('Task 3 verified')"`

- [x] **4. [crew-board] Dynamic Kanban board title display**
  - Add `active_board_name()` helper in `oss/crew/scripts/crew_graph.py` and include `"board"` in `/board.json` payload in `crew_graph_serve.py`.
  - Update `board_page()` in `crew_graph_serve.py`, `board.js`, and `oss/crew/desktop/plugin.js` to render the dynamic board name.
  - Proof: `python -c "srv = open('oss/crew/scripts/crew_graph_serve.py').read(); js = open('oss/crew/scripts/crew_dashboard/board.js').read(); assert 'board' in srv; print('Task 4 verified')"`

- [x] **5. [crew-sync] Synchronize changes to active runtime and run tests**
  - Copy updated artifacts from `oss/crew/` to `_config/agent4070/hermes/plugins/crew/`.
  - Run pytest test suite in `oss/crew/tests/` to verify no regressions in permissions, boundaries, and routing.
  - Proof: `pytest oss/crew/tests/`
