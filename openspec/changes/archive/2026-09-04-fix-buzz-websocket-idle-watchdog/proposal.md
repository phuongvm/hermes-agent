# Change: Fix Buzz Platform WebSocket Idle Disconnect Watchdog

## Why
When Hermes Gateway runs the Buzz platform adapter (`plugins/platforms/buzz/adapter.py`), the WebSocket connection disconnects and reconnects periodically every ~302 seconds (300s timeout + 1s backoff + 1-2s reconnect) when channels are idle:
```text
WARNING hermes_plugins.buzz_platform.adapter: Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s; assuming the connection went silent
```
Over 9,180 occurrences were recorded in runtime logs.

Commit `94d86fa4de` (#98097) introduced `_WS_READ_IDLE_TIMEOUT = 300.0` wrapped around `websocket.__aiter__().__anext__()` to catch silent TCP socket deadlocks (`CLOSE_WAIT`). However, in `websockets` (Sans-I/O v15.0.1), `__anext__()` and `recv()` only return **Data Frames** (`TEXT`, `BINARY`, `CONT`). WebSocket keepalive control frames (`PING` / `PONG`), which exchange successfully every 20 seconds (`ping_interval=20, ping_timeout=20`), are handled internally by `Connection.process_event()` and are **never yielded** to `__anext__()`.

Consequently, in quiet channels without incoming user chat messages for 5 minutes, `frame_iter.__anext__()` raises `asyncio.TimeoutError`, causing the adapter to falsely treat a healthy, live connection as dead, sever the socket, log a spurious warning, and repeatedly re-authenticate and re-subscribe.

## What Changes
- **Distinguish Data Silence from Socket Silence**: Update the watchdog in `_websocket_loop` so that channel silence (no chat events) does not trigger a disconnect while WebSocket transport keepalive is healthy.
- **Active Ping/Keepalive Check on Idle**: When `frame_iter.__anext__()` times out after `_WS_READ_IDLE_TIMEOUT`, probe the connection's liveness (e.g. via `websocket.ping()` with a short timeout, or by inspecting keepalive status) instead of immediately assuming disconnection. If the ping succeeds, reset the idle timer and continue reading; only raise `ConnectionError` if the probe actually fails or times out.
- **Demote Spurious Warnings**: Ensure that benign idle resets or scheduled keepalive refreshes do not pollute logs with `WARNING` level noise.
- **Regression Tests**: Add test coverage in `tests/gateway/test_buzz_websocket.py` demonstrating that a silent channel with a responding WebSocket server remains connected across multiple idle intervals without raising `ConnectionError` or disconnecting.
