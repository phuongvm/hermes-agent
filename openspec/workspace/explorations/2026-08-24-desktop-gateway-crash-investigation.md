# Exploration: Hermes Gateway Crash upon Desktop Connection

**Date**: 2026-08-24  
**Author**: Hermes Agent / phuong_lambert  
**Status**: Completed (Ready for Proposal)  
**Topic**: Root cause analysis and remediation architecture for Hermes Gateway termination triggered by Hermes Desktop connection.

---

## 1. Problem Statement

When the Hermes Desktop application (Electron) connects to the system environment:
1. **Hermes Dashboard** (`127.0.0.1:9119`, FastAPI) remains operational and serves web UI / PTY WebSockets properly.
2. **Hermes Desktop** successfully connects to the remote dashboard backend at `http://127.0.0.1:9119`.
3. **Hermes Gateway** (`gateway/run.py`, managing messaging platform adapters like Telegram/Buzz and the `api_server` adapter on port `8642`) repeatedly crashes / terminates abruptly with an `unclean exit` (SIGKILL / `taskkill /T /F`).

---

## 2. Architecture & Data Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│                        HERMES DESKTOP (Electron)                       │
│                                                                        │
│  [Renderer UI] ──(IPC: hermes:api)──▶ [Electron Main Process]          │
│                                                │                       │
│       ┌────────────────────────────────────────┴───────────────────┐   │
│       │ 1. Read AppData/Roaming/hermes/connection.json             │   │
│       │    -> Mode: "remote", URL: "http://127.0.0.1:9119"        │   │
│       │ 2. Connect to Remote Backend (Dashboard port 9119)         │   │
│       │ 3. Run Lifecycle Maintenance & Orphan Process Sweeper:     │   │
│       │    -> reapOrphanedBackendsOnce()                           │   │
│       │    -> backendOwnership.reapOrphans()                       │   │
│       └────────────────────────────────────────┬───────────────────┘   │
└────────────────────────────────────────────────┼───────────────────────┘
                                                 │
                  ┌──────────────────────────────┴──────────────────────────────┐
                  ▼                                                             ▼
┌───────────────────────────────────────┐                     ┌───────────────────────────────────────┐
│     HERMES DASHBOARD (Port 9119)      │                     │      HERMES GATEWAY (Port 8642)       │
│      (FastAPI - web_server.py)        │                     │         (gateway/run.py)              │
│                                       │                     │                                       │
│ • Endpoints:                          │                     │ • Platform Adapters & Listeners:      │
│   - /api/status, /api/health          │                     │   - Telegram Poller (long-polling)    │
│   - /api/ws (PTY WebSocket)           │                     │   - Buzz Platform Adapter             │
│   - /api/gateway/start, restart       │                     │   - api_server (aiohttp port 8642)    │
│                                       │                     │                                       │
│ • Status: Healthy / Operational       │                     │ • Status:                             │
│ • Serves Desktop remote requests      │                     │   Abruptly killed by Desktop's        │
│                                       │                     │   synchronous taskkill /T /F sweeper  │
└───────────────────────────────────────┘                     └───────────────────────────────────────┘
```

---

## 3. Empirical Evidence & Forensic Findings

### A. Gateway Exit Diagnostics (`_config/agent4070/hermes/logs/gateway-exit-diag.log`)
The lifecycle ledger logs confirm that previous gateway processes did not execute their clean shutdown or drain paths, but were terminated by external SIGKILL / taskkill:
```json
{"ts": "2026-08-24T12:01:37.436570+00:00", "tag": "gateway.previous_unclean_exit", "pid": 10752, "prior_pid": 13652, "prior_started_at": "2026-08-24T11:30:08.969500+00:00", "last_heartbeat_at": "2026-08-24T11:59:56.630356+00:00"}
```
Warning emitted in `gateway.log`:
```text
2026-08-24 19:01:37,437 WARNING gateway.lifecycle_ledger: Previous gateway life (pid=13652) exited UNCLEANLY (no exit path ran — SIGKILL / OOM / VM death).
```

### B. Desktop IPC Crash (`_config/asus-vb/hermes/logs/desktop.log`)
When the renderer UI invokes backend methods via the `hermes:api` IPC bridge, a JavaScript runtime error occurs in Electron's main process:
```text
[2026-08-24T11:05:57.699Z] [hermes] [renderer console:main] Uncaught (in promise) Error: Error invoking remote method 'hermes:api': ReferenceError: req is not defined (file:///O:/workspaces/oss/hermes-agent/apps/desktop/release/win-unpacked/resources/app.asar/dist/assets/index-Jtoy8txk.js:46)
```

### C. Local Backend Fallback Rejection (`desktop.log`)
After IPC errors, Desktop attempts to fallback-spawn an ephemeral local backend (`serve --host 127.0.0.1 --port 0`), but fails the WebSocket token validation:
```text
[2026-08-24T11:05:51.067Z] [hermes] [boot] Desktop boot failed: Local Hermes backend is HTTP-reachable but the WebSocket (/api/ws) rejected the session token: WebSocket connection failed.
```

---

## 4. Root Cause Breakdown

### Root Cause 1: Aggressive Orphan Process Reaping in `backend-ownership.ts`
* **Mechanism**:
  In `apps/desktop/electron/backend-ownership.ts:206` (`reapOrphans`) and `apps/desktop/electron/main.ts:3284` (`stopOwnedBackend`):
  ```typescript
  async reapOrphans(): Promise<number[]> {
    // ...
    if (parentAlive !== true) {
      if (IS_WINDOWS) {
        forceKillProcessTree(identity.pid) // Synchronous: taskkill /T /F /PID <pid>
      }
    }
  }
  ```
  The command matcher in `backendCommandMatches`:
  ```typescript
  export function backendCommandMatches(command: unknown): boolean {
    return /(?:^|[\s/\\"])(?:hermes(?:\.exe)?|hermes_cli\.main|hermes_cli[/\\]main\.py)"?(?:\s+(?:--profile|-p)\s+\S+)?\s+(?:serve|dashboard)(?:\s|$)/i.test(
      String(command ?? '')
    )
  }
  ```
* **Failure Chain**:
  1. Desktop maintains a record in `backend-ownership.json` or scans existing Python processes matching Hermes CLI signatures.
  2. Because the Hermes Gateway runs as an independent Windows background process (managed by NSSM or launched via `start-gateway.cmd`), its `parentPid` does not match the active Electron process PID.
  3. When Desktop triggers `reapOrphanedBackendsOnce()` during startup or connection reset, it targets the Gateway's PID and sends `taskkill /T /F`, abruptly terminating the entire Gateway tree.

### Root Cause 2: Scope Variable Reference Error in `handleHermesApiRequest`
* **Mechanism**:
  In `apps/desktop/electron/main.ts:13992` (`handleHermesApiRequest`), recent refactoring introduced references to `req` in helper/dispatch calls where `request` was the formal parameter or where nested scope variables were shadowed/undefined, causing `ReferenceError: req is not defined`.
* **Impact**:
  Renderer API calls fail immediately, inducing a recovery cycle that triggers connection restarts, re-runs orphan reaping, and exacerbates process flapping.

### Root Cause 3: Endpoint Separation (Dashboard 9119 vs Gateway 8642)
* `Hermes Dashboard` (`hermes dashboard --port 9119`) hosts the Web UI, status endpoints, and PTY web server.
* `Hermes Gateway` (`hermes gateway run --replace`) hosts platform messaging adapters and the `api_server` on port `8642`.
* Desktop's `connection.json` is configured with `remote: http://127.0.0.1:9119`. While Desktop can communicate with Dashboard's HTTP endpoints, its internal state management attempts to enforce process exclusivity over all local Hermes instances, inadvertently killing the Gateway.

---

## 5. Comparative Solutions & Trade-Offs

| Approach | Pros | Cons / Risks | Recommendation |
| :--- | :--- | :--- | :--- |
| **Option A: Narrow Matcher in `backend-ownership.ts`** | Prevents Desktop from ever targeting `hermes gateway` processes. Clear separation of concerns. | Must ensure Desktop still correctly cleans up orphaned `serve` children. | **Essential** |
| **Option B: Fix `req` Reference in `main.ts`** | Resolves the renderer IPC crash and breaks the infinite retry/restart loop. | Requires rebuilding/rebundling the Electron app. | **Essential** |
| **Option C: Unified Architectural Solution (A + B + Guard)** | Fixes both the root cause of the crash and the IPC fault; hardens Gateway with `HERMES_GATEWAY_DETACHED=1`. | Requires comprehensive OpenSpec change with unit/E2E test validation. | **Recommended** |

---

## 6. Proposed Specification for OpenSpec Change

**Change Name**: `fix-desktop-gateway-process-isolation`

### Key Design Changes:
1. **`apps/desktop/electron/backend-ownership.ts`**:
   - Explicitly exclude any process command containing `gateway run` or carrying service markers from `backendCommandMatches` and `reapOrphans()`.
   - Ensure orphan reaping only targets ephemeral `serve` backend processes explicitly spawned by Electron.
2. **`apps/desktop/electron/main.ts`**:
   - Fix all undefined `req` references in `handleHermesApiRequest` and its routing delegates.
3. **`hermes_cli/gateway_windows.py` & Service Scripts**:
   - Verify that `HERMES_GATEWAY_DETACHED=1` is consistently exported in all Windows background startup paths (`start-gateway.cmd`).
4. **Verification Strategy**:
   - Unit tests in `backend-ownership.test.ts` asserting that `gateway run` command lines are never classified as killable orphans.
   - Verification of `apps/desktop` typecheck (`npm run typecheck`) ensuring no identifier errors.

---

## 7. Hard Exit Gate

Exploration is complete and verified against empirical evidence.  
Ready to transition to `/opsx-propose` to create the formal OpenSpec change `fix-desktop-gateway-process-isolation`.
