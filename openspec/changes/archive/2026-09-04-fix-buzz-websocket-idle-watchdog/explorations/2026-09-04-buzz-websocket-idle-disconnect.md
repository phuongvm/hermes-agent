# OpenSpec Exploration: Buzz Platform WebSocket Idle Disconnect & Keepalive Watchdog

**Date**: 2026-09-04  
**Status**: Exploration / Root Cause Investigation Complete  
**Project**: `hermes-agent` (`O:\workspaces\oss\hermes-agent`)  
**Scope**: Buzz Platform Adapter (`plugins/platforms/buzz/adapter.py`) & Gateway WebSocket Transport  

---

## 1. Executive Summary & Incident Manifest

### 1.1 Observed Warning Log
In the Hermes Gateway runtime logs (`logs/gateway.log`):
```text
2026-09-04 08:31:18,107 WARNING hermes_plugins.buzz_platform.adapter: Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s; assuming the connection went silent
```

### 1.2 Quantitative Evidence & Recurring Pattern
Auditing `gateway.log` reveals **9,180 occurrences** of this exact warning, executing on a strict ~302-second recurring cadence:

| Timestamp (UTC+7) | Interval | Log Event |
| :--- | :---: | :--- |
| `08:01:03,666` | — | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |
| `08:06:06,017` | +302.35s | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |
| `08:11:09,776` | +303.76s | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |
| `08:16:11,591` | +301.81s | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |
| `08:21:13,620` | +302.03s | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |
| `08:26:15,501` | +301.88s | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |
| `08:31:18,107` | +302.60s | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |
| `08:36:20,073` | +301.96s | Buzz: WebSocket disconnected; retrying in 1.0s: no WebSocket frame for 300s |

The interval matches the exact sum:
$$\Delta t = T_{\text{idle\_timeout}} (300\text{s}) + T_{\text{backoff}} (1\text{s}) + T_{\text{reconnect\_auth\_sub}} (\approx 1\text{--}2\text{s}) \approx 302\text{s}$$

---

## 2. Deep Root Cause Analysis

### 2.1 Code Origin: Commit `94d86fa4de` (Issue #98097)
Commit `94d86fa4de` was introduced to fix issue `#98097`:
> *"A relay-side close the transport never surfaces (observed as a CLOSE_WAIT socket behind Cloudflare, #98097) parks the read loop forever while the gateway keeps reporting connected: inbound stops, gateway_state.json stays healthy, and only a restart recovers."*

To bound silent read hangs, `adapter.py` added:
```python
_WS_READ_IDLE_TIMEOUT = 300.0  # 5 minutes
```
And modified the reader loop in `_websocket_loop()`:
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

### 2.2 Protocol Violation: Data Frames vs Control Frames in `websockets`
The fundamental design flaw stems from how the `websockets` library (v15.0.1 Sans-I/O architecture) handles frames:

```
[Incoming TCP Packet from Relay]
               │
               ▼
   [Connection.data_received()]
               │
   [Connection.process_event()]
               │
       ┌───────┴────────────────────────┐
       │                                │
[event.opcode in DATA_OPCODES]   [event.opcode is Opcode.PONG]
 (TEXT: 1, BINARY: 2, CONT: 0)            (PONG: 10)
       │                                │
       ▼                                ▼
[self.recv_messages.put()]    [self.acknowledge_pings()]
       │                                │
       ▼                                ▼
[websocket.__aiter__() / recv()]  (Internal state only;
 (Consumes ONLY Data Frames)       NEVER surfaced to __anext__)
```

1. **Keepalive is Healthy**:
   The client runs keepalive pings via `ping_interval=20, ping_timeout=20`. Every 20 seconds, the client sends a `PING` frame, and the relay responds with a `PONG` frame. The TCP socket is 100% active and healthy.
2. **Control Frames are Filtered Out**:
   `websocket.__aiter__()` calls `recv()`, which reads strictly from `self.recv_messages`. `self.recv_messages` **only receives DATA frames** (`TEXT` / `BINARY`). Control frames (`PONG`) are processed internally to reset ping timeouts and are dropped from the message queue.
3. **The False Positive**:
   When a Buzz channel or direct message thread has no new chat messages for 300 seconds (which is completely normal in quiet rooms or overnight), `frame_iter.__anext__()` never returns a frame.
4. **False Alarm Triggered**:
   `asyncio.wait_for(..., timeout=300.0)` triggers `asyncio.TimeoutError`, which raises `ConnectionError("no WebSocket frame for 300s; assuming the connection went silent")`. The adapter assumes the connection died, drops the healthy connection, logs a `WARNING`, waits 1.0s, and re-connects.

---

## 3. Impact Assessment

| Category | Impact Level | Description |
| :--- | :---: | :--- |
| **Data Loss** | **None (Safe)** | The adapter persists and uses high-water cursor marks (`_cursor_mark`, `since` filter) across reconnects, so no chat events are lost. |
| **Relay & Client Load** | **Moderate Waste** | Every 5 minutes, the adapter re-initiates NIP-42 Schnorr auth challenge-response and re-sends REQ filters across all watched channels. |
| **Log Pollution** | **Severe** | Over 9,000 WARNING entries cluttering `gateway.log`, obscuring legitimate operational errors. |
| **Transient Latency** | **Minor** | During the 1-2 second reconnect window, any incoming relay messages may experience slight buffering delays. |

---

## 4. Architectural Alternatives & Tradeoff Matrix

```
┌────────────────────────────────────────────────────────────────────────┐
│                              Option Space                              │
├─────────────────────────┬──────────────────────┬───────────────────────┤
│ Approach                │ Pros                 │ Cons                  │
├─────────────────────────┼──────────────────────┼───────────────────────┤
│ Option 1: Native WS     │ Standard RFC6455;    │ Might not catch a     │
│ Keepalive Trust         │ zero log noise;      │ half-open TCP socket  │
│ (Remove idle watchdog)  │ zero churn.          │ if keepalive is slow  │
│                         │                      │ behind buggy proxies. │
├─────────────────────────┼──────────────────────┼───────────────────────┤
│ Option 2: Active Socket │ Inspects actual      │ Couples to websockets │
│ Activity / Pong Heartbeat│ keepalive liveness; │ internals or requires │
│ Inspection              │ perfectly detects    │ tracking last_pong /  │
│                         │ silent drops.        │ ping roundtrip.       │
├─────────────────────────┼──────────────────────┼───────────────────────┤
│ Option 3: Configurable  │ Backward compatible; │ Still disconnects if  │
│ Idle Timeout + Demote   │ eliminates false     │ timeout is exceeded;  │
│ Warning to Debug        │ alarms in logs.      │ does not fix concept. │
└─────────────────────────┴──────────────────────┴───────────────────────┘
```

### Proposed Technical Solution: Option 2 Hybrid with Robust Fallback
1. **Differentiate Channel Silence from Socket Death**:
   - In `_websocket_loop`, do not treat lack of incoming *chat data* as a socket failure if WebSocket keepalive pings/pongs are succeeding.
   - Alternatively, use WebSocket client's ping mechanism explicitly or monitor `websocket.latency` / connection state.
2. **Ensure Clean Watchdog Semantics**:
   - If a watchdog is strictly required for Cloudflare/proxy deadlocks, either:
     - Check whether `websocket.ping()` succeeds when idle, OR
     - Send a lightweight Nostr REQ or rely on `websockets` built-in `ping_interval=20, ping_timeout=20` which already terminates the connection with `ConnectionClosedError` if a pong is missed.
3. **Log Level Demotion & Clean Reporting**:
   - Demote expected/proactive idle cycling to `DEBUG` or `INFO` rather than `WARNING` if deliberate disconnection is retained.

---

## 5. Next Steps & Exit Gate Readiness
- [x] Problem verified with quantitative log evidence and commit history.
- [x] Root cause traced down to Sans-I/O `websockets` frame dispatching mechanics.
- [x] Exploration document finalized at `openspec/workspace/explorations/2026-09-04-buzz-websocket-idle-disconnect.md`.
- Ready to proceed to `/opsx-propose` (e.g. `fix-buzz-websocket-idle-watchdog`) when authorized.
