# Design: Buzz WebSocket Idle Watchdog Liveness Verification

## 1. Problem Statement
The reader loop in `plugins/platforms/buzz/adapter.py`:
```python
frame_iter = websocket.__aiter__()
while True:
    try:
        raw = await asyncio.wait_for(
            frame_iter.__anext__(),
            timeout=_WS_READ_IDLE_TIMEOUT,
        )
    except StopAsyncIteration:
        break
    except asyncio.TimeoutError:
        raise ConnectionError(
            f"no WebSocket frame for {_WS_READ_IDLE_TIMEOUT:.0f}s; "
            "assuming the connection went silent"
        ) from None
```
`frame_iter.__anext__()` yields only incoming WebSocket **Data Frames** (Nostr JSON events). It does NOT yield `PONG` frames resulting from background `ping_interval=20` keepalive pings.
When no user sends messages for 300 seconds, `asyncio.TimeoutError` fires and unconditionally severs the connection.

## 2. Technical Approach: Active Liveness Probe on Timeout

Instead of immediately raising `ConnectionError` when `__anext__()` times out after `_WS_READ_IDLE_TIMEOUT`, the adapter executes an explicit liveness probe:

```
                  ┌──────────────────────────────┐
                  │ frame_iter.__anext__()       │
                  │ timeout=_WS_READ_IDLE_TIMEOUT│
                  └──────────────┬───────────────┘
                                 │
                   TimeoutError (No data for 300s)
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │ Probe Connection Liveness    │
                  │ pong_waiter = await ping()   │
                  │ wait_for(pong, timeout=10.0) │
                  └──────────────┬───────────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 │                               │
             Pong OK                         Failed / Timed Out
                 │                               │
                 ▼                               ▼
       Log debug (keepalive ok)        raise ConnectionError(
       Continue while loop             "no WebSocket frame and ping failed")
       (Socket preserved)              (Trigger clean reconnect)
```

### 2.1. Implementation Details
In `plugins/platforms/buzz/adapter.py`:
1. When `asyncio.TimeoutError` occurs during `frame_iter.__anext__()`:
   ```python
   except asyncio.TimeoutError:
       # No data message arrived for _WS_READ_IDLE_TIMEOUT.
       # The relay may simply be quiet (no chat messages).
       # Send an explicit ping to test if the TCP connection is still alive.
       try:
           pong_waiter = await websocket.ping()
           await asyncio.wait_for(pong_waiter, timeout=10.0)
           logger.debug("Buzz: WebSocket idle for %.0fs; keepalive probe succeeded", _WS_READ_IDLE_TIMEOUT)
           continue
       except Exception as ping_err:
           raise ConnectionError(
               f"no WebSocket frame for {_WS_READ_IDLE_TIMEOUT:.0f}s and "
               f"keepalive probe failed: {ping_err}"
           ) from None
   ```
2. If `ping()` succeeds, the socket is proven alive and the loop resumes waiting for frames.
3. If `ping()` fails or times out after 10s, the socket is genuinely dead (or stuck in `CLOSE_WAIT`), and `ConnectionError` is raised to trigger the reconnect backoff path as originally intended by #98097.

## 3. Backward Compatibility & Invariants
- Preserves the original goal of #98097: dead sockets behind Cloudflare/reverse proxies will still fail the `ping()` probe within 10s and trigger reconnect.
- Healthy idle sockets remain connected indefinitely without reconnect thrashing, NIP-42 auth storms, or log spam.
- Preserves cursor marks and subscriptions.
