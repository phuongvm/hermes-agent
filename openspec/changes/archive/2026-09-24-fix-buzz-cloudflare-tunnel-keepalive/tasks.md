# Tasks: Buzz WebSocket Application Keepalive

## 1. Implementation (`plugins/platforms/buzz/adapter.py`)
- [x] 1.1 Declare `_WS_KEEPALIVE_SUB_ID` and `_DEFAULT_WS_APP_KEEPALIVE_INTERVAL` constants
- [x] 1.2 Parse and clamp `ws_app_keepalive_interval` in `BuzzAdapter.__init__` (min 10.0s)
- [x] 1.3 Implement `_ws_keepalive_loop(self, websocket)` sending REQ and CLOSE frames
- [x] 1.4 Wire `_ws_keepalive_loop` task into `_websocket_loop`
- [x] 1.5 Handle `CLOSED`, `EOSE`, and `OK` in `_handle_ws_message` without dropping channels or warning

## 2. Regression Testing (`tests/gateway/test_buzz_websocket.py`)
- [x] 2.1 Verify `test_websocket_app_keepalive_loop_sends_req_and_close` passes
- [x] 2.2 Verify `test_websocket_app_keepalive_closed_frame_ignored` passes

## 3. Verification & Compliance
- [x] 3.1 Run `pytest tests/gateway/test_buzz_websocket.py` and confirm all tests pass
- [x] 3.2 Validate change with `openspec validate fix-buzz-cloudflare-tunnel-keepalive --strict`
