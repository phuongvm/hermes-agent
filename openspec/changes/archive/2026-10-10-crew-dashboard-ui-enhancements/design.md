# Technical Design: Crew Dashboard UI & Theme Enhancements

## 1. Architectural Overview
This change refines the communication bridge and visual representation of the Crew coordination board within Hermes Desktop:
```
┌─────────────────────────────────────────────────────────────┐
│ Hermes Desktop Renderer (React ESM Plugin)                  │
│   src/plugins/crew/desktop/plugin.js                        │
│   - Reads computed CSS vars (--dt-background, --dt-card...) │
│   - Injects query params & listens to theme epoch           │
│   - Sends postMessage({ type: 'hermes:theme-change' })     │
└──────────────────────────┬──────────────────────────────────┘
                           │ postMessage & query params
┌──────────────────────────▼──────────────────────────────────┐
│ Crew Board Frontend (Iframe at /api/plugins/crew/board)     │
│   - crew.css: .card.live / .card.quiet conic-gradient radar │
│   - tokens.css: dynamic root variable binding               │
│   - board.js: postMessage theme listener, status motion     │
└──────────────────────────┬──────────────────────────────────┘
                           │ fetch /ack/all, /board.json
┌──────────────────────────▼──────────────────────────────────┐
│ FastAPI Gateway Proxy (`plugin_api.py`)                      │
│   - Stripped of hardcoded dark CSS                          │
│   - Forwards loopback headers (X-Forwarded-Host, Origin)    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ Crew Graph Daemon (`crew_graph_serve.py` on :8799)          │
│   - _same_origin(): permits loopback 127.0.0.1 proxies      │
│   - Exposes active board slug in board_data()               │
└─────────────────────────────────────────────────────────────┘
```

## 2. Key Seams & Implementation Details

### 2.1 Radar Animation
- In `board.js`, card motion class determination:
  ```javascript
  var isRunning = t.status === "running";
  var motion = isRunning
    ? (t.active && t.active.quiet_s !== null && t.active.quiet_s > QUIET_S ? " quiet" : " live")
    : "";
  ```
- In `crew.css`:
  Ensure the `@keyframes` and `conic-gradient` mask syntax work cleanly without depending solely on CSS Houdini properties if `@property` is restricted in certain iframe contexts.

### 2.2 Theme Bridge
- In `desktop/plugin.js`:
  Read `getComputedStyle(document.documentElement)`:
  - `--dt-background` / `--ui-bg-editor`
  - `--dt-card`
  - `--ui-text-primary`
  - `--ui-stroke-tertiary`
  - `document.documentElement.dataset.hermesMode`
  Pass via initial URL: `?theme=${mode}&bg=${encodeURIComponent(bg)}&fg=${encodeURIComponent(fg)}`
  And emit `iframe.contentWindow.postMessage({ type: 'hermes:theme', ... }, '*')` on `themeEpoch` change.
- In `board.js`:
  Listen for `window.addEventListener('message', (e) => { ... })` and update document style properties dynamically.

### 2.3 Loopback ACK Permission
- In `crew_graph_serve.py`:
  Update `_same_origin()`:
  ```python
  def _same_origin(self):
      sec_site = self.headers.get("Sec-Fetch-Site")
      if sec_site and sec_site not in ("same-origin", "none", "same-site"):
          # Allow loopback cross-site between local ports
          src = self.headers.get("Origin") or self.headers.get("Referer") or ""
          src_host = urlparse(src).hostname
          if src_host in ("127.0.0.1", "localhost"):
              return True
          return False
      src = self.headers.get("Origin") or self.headers.get("Referer") or ""
      if not src:
          return True
      src_host = urlparse(src).hostname
      host = (self.headers.get("Host") or "").split(":")[0]
      return src_host in ("127.0.0.1", "localhost") and host in ("127.0.0.1", "localhost")
  ```

### 2.4 Dynamic Board Title
- In `crew_graph.py`:
  ```python
  def active_board_name():
      cur = current_board_path()
      ...
  ```
  Expose `"board": active_board_name()` in `/board.json`.
- In `crew_graph_serve.py`:
  Render `<h1>%s Board</h1>` formatted dynamically.
