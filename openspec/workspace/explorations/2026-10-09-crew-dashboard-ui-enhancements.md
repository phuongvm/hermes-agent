# Exploration: Crew Dashboard UI & Theme Enhancements in Hermes Desktop

- **Date**: 2026-10-09
- **Topic**: Radar Animation, Dynamic Theme Synchronization, Notification Clear-All, and Board Title Resolution
- **Target Repositories**: `oss/hermes-agent` and `oss/crew` (with runtime sync to `_config/agent4070/hermes/plugins/crew/`)

## 1. Problem Definition & Empirical Evidence

### Enhancement 1: Radar Gradient Animation on Active (Running) Tasks
- **Root Cause**:
  - In `oss/crew/scripts/crew_dashboard/board.js` (line 99), `.card.live` motion class is attached only if `t.active` is non-null.
  - In `oss/crew/scripts/crew_graph_serve.py` (lines 193–202), `t.active` requires `r[3] == 'running'` AND `run_is_live(r, now)`. When tasks are marked `running` in Kanban DB but lack an active PID or recent heartbeat, `t.active` returns `null`, causing the card to render completely static.
  - Furthermore, `crew.css` uses `@property --crew-arc` with `conic-gradient(from var(--crew-arc), transparent 0 70%, var(--arc) 88%, transparent 100%)`. In contrast, Hermes Desktop's kanban styling (`apps/desktop/src/plugins/kanban/kanban.css`) uses a dedicated overlay `.kanban-arc` with explicit `55deg` / `110deg` stops that renders reliably across Chromium / Electron frames.
- **Remediation**:
  - Ensure all cards in `running` status trigger the animated sweep overlay.
  - Align `crew.css` arc overlay with the tested desktop kanban radar sweep pattern.

### Enhancement 2: Dynamic Theme Background & Foreground Synchronization
- **Root Cause**:
  - In `oss/crew/dashboard/plugin_api.py` (lines 81–107), the backend proxy forcibly injects `<style id="hermes-theme-sync">` with hardcoded dark styles:
    `:root { --color-background: #041c1c !important; --crew-bg: #041c1c !important; }` and `html, body, main#board { background-color: #041c1c !important; }`.
  - In `oss/crew/desktop/plugin.js` (lines 71–94), `syncTheme()` attempts direct DOM access (`iframe.contentDocument`), which throws cross-origin security errors when loading via `http://127.0.0.1:8799/`.
  - In `oss/crew/scripts/crew_dashboard/tokens.css`, `--crew-scheme` defaults to `dark` with fallback `#041c1c`.
- **Remediation**:
  - Remove hardcoded `#041c1c !important` from `plugin_api.py`.
  - In `plugin.js`, collect theme tokens using Hermes Desktop standard (`--dt-background`, `--dt-card`, `--ui-text-primary`, `--ui-stroke-tertiary`, color-scheme) and pass them via URL query parameters AND runtime `postMessage` (`{ type: 'hermes:theme-change', ... }`).
  - In `board.js` / `tokens.css`, listen for `postMessage` and apply the theme dynamically without page refresh.

### Enhancement 3: 'Clear All' Notifications Fix
- **Root Cause**:
  - Clicking 'Clear All' triggers `fetch("/ack/all", { method: "POST" })`.
  - In `crew_graph_serve.py` (lines 621–636), `do_POST` enforces `_same_origin()`:
    It verifies `urlparse(Origin or Referer).netloc.lower() == Host.lower()`.
    When proxied through `plugin_api.py` (port 9119) or accessed from Desktop Electron (`app://` or cross-port), `Origin` netloc (`127.0.0.1:9119`) does NOT match upstream `Host` (`127.0.0.1:8799`), causing `_same_origin()` to return `False` and respond with `403 cross-site write refused`.
  - `board.js` fails to check `response.ok` on the fetch promise, so `.then(tick)` proceeds to poll the unchanged data silently.
- **Remediation**:
  - In `crew_graph_serve.py`, update `_same_origin()` to permit requests originating from loopback origins (`127.0.0.1`, `localhost`, `app://`) and allow loopback proxy headers (`X-Forwarded-Host`).
  - In `plugin_api.py`, ensure forward requests correctly forward or adapt loopback headers.
  - In `board.js`, check `response.ok` and handle errors gracefully.

### Enhancement 4: Dynamic Kanban Board Title Display
- **Root Cause**:
  - In `crew_graph_serve.py` (lines 404, 415), the title is hardcoded: `<title>crew board</title>` and `<h1>crew board</h1>`.
  - In `desktop/plugin.js` (line 150), header text is hardcoded: `Crew Coordination Board`.
  - The payload returned by `/board.json` does not expose the active board slug or name.
- **Remediation**:
  - In `crew_graph.py` / `crew_graph_serve.py`, resolve the active board slug via `current_board_path()` (`<root>/kanban/current`), `HERMES_KANBAN_BOARD`, or DB resolution.
  - Expose `"board": slug` in `/board.json`.
  - Update `board_page()` and `board.js` to render the dynamic board title in `<h1>` and `<title>`.
  - Update `desktop/plugin.js` to reflect the dynamic board title.
