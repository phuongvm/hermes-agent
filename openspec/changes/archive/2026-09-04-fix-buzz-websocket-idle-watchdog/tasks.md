# Tasks: Fix Buzz WebSocket Idle Disconnect Watchdog

## Milestones

- [x] 1. Core Adapter Liveness Probe Fix <!-- id: 1 -->
  - [x] 1.1 Update `_websocket_loop` in `plugins/platforms/buzz/adapter.py` to probe connection liveness via `websocket.ping()` when `__anext__()` times out after `_WS_READ_IDLE_TIMEOUT` <!-- id: 1.1 -->
  - [x] 1.2 Only raise `ConnectionError` if the keepalive probe fails or times out, otherwise continue reading <!-- id: 1.2 -->

- [x] 2. Test Suite & Verification <!-- id: 2 -->
  - [x] 2.1 Update `tests/gateway/test_buzz_websocket.py` to mock `ping()` on `_ScriptedWebSocket` <!-- id: 2.1 -->
  - [x] 2.2 Add unit test verifying that a silent channel with responsive `ping()` stays connected across idle timeouts without disconnecting or logging warnings <!-- id: 2.2 -->
  - [x] 2.3 Add unit test verifying that when `ping()` also times out or fails on idle, `ConnectionError` is raised and reconnect is triggered (preserving #98097 behavior) <!-- id: 2.3 -->
  - [x] 2.4 Run complete Buzz gateway test suite and verify all tests pass <!-- id: 2.4 -->

## Verification Evidence
- Task 1.1 & 1.2: Implemented active ping probe upon idle timeout in `plugins/platforms/buzz/adapter.py`.
- Task 2.1: Updated `_ScriptedWebSocket` mock to support configurable `ping_behavior`.
- Task 2.2: Added `test_websocket_loop_idle_silent_channel_keeps_connection_alive_when_ping_succeeds` (asserting zero warnings, ping_count >= 2, connection remains open).
- Task 2.3: Added parameterized test `test_websocket_loop_reconnects_when_ping_fails_or_times_out` across connection reset, runtime error, and timeout.
- Task 2.4: `pytest tests/gateway/test_buzz_websocket.py`: 22/22 passed in 1.95s.
