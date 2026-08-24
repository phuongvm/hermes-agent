## Why

When Hermes Desktop (Electron app) connects in local or remote modes, it inadvertently triggers the abrupt termination of independent Hermes Gateway processes running on the same host (e.g. Windows services managed via NSSM or background CLI runs). This occurs due to two coupled root causes:

1. **Unconstrained Backend Reaping**: Desktop's backend ownership subsystem (`apps/desktop/electron/backend-ownership.ts`) sweeps for orphaned Python processes matching generic Hermes commands (`hermes_cli.main`) and invokes synchronous tree-kill (`taskkill /T /F` on Windows) on any process whose `parentPid` does not match the current Electron process. Independent Gateway processes are erroneously identified as orphans and terminated.
2. **IPC Scope Variable Errors**: Recent refactoring in `apps/desktop/electron/main.ts` inside `handleHermesApiRequest` introduced runtime `ReferenceError: req is not defined` exceptions on incoming IPC requests from the renderer. When IPC crashes, the renderer assumes connection failure and triggers aggressive reconnection and local backend fallback spawn cycles, accelerating the orphan reaping sweep.

This change isolates Desktop process management so that independent Gateway processes are strictly excluded from reaping, and fixes the IPC parameter resolution in Desktop's main process.

## What Changes

- **Desktop Backend Command Matcher Isolation**:
  - Update `backendCommandMatches` in `apps/desktop/electron/backend-ownership.ts` to strictly require `serve` or `dashboard --no-open` commands explicitly owned by Desktop, and actively exclude commands containing `gateway run`.
  - Ensure `reapOrphans` never signals or kills processes designated as Gateway or background services.
- **Desktop Main IPC Scope Fix**:
  - Audit and fix all `request` / `req` parameter references in `apps/desktop/electron/main.ts` (`handleHermesApiRequest`, `dispatchApiRequestRoute`, `resolveRegistry`, and `resolveLegacy`) to ensure no `ReferenceError` occurs during IPC message handling.
- **Gateway Windows Service Detached Guard**:
  - Verify and enforce `HERMES_GATEWAY_DETACHED=1` in service startup scripts and CLI detached launchers (`hermes_cli/gateway_windows.py`) to prevent interference from sibling console broadcasts.
- **Comprehensive Unit Testing**:
  - Add test coverage in `backend-ownership.test.ts` verifying that `gateway run` commands with or without profile flags evaluate to `false` in `backendCommandMatches`.
  - Add test coverage in `connection-config.test.ts` ensuring IPC request routing delegates handle payloads without reference errors.

## Capabilities

### Modified Capabilities
- `desktop-backend-lifecycle`: Hardens process ownership detection and orphan reaping to ensure process isolation between Desktop and Gateway.
- `desktop-ipc-routing`: Ensures error-free IPC handling for `hermes:api` requests across local and remote profiles.

## Impact

- **Desktop**: Eliminates `ReferenceError` crashes in renderer-to-main IPC communication; stabilizes connection lifecycle.
- **Gateway**: Protects long-running messaging adapters (Telegram, Buzz, WhatsApp, API Server) from unintended termination when Desktop launches or reconnects.
- **Codebase**: Touches `apps/desktop/electron/backend-ownership.ts`, `apps/desktop/electron/main.ts`, and associated unit test suites.
- **Dependencies**: No new external dependencies.
