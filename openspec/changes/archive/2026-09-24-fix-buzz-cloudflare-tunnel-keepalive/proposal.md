# Proposal: Buzz WebSocket Application Keepalive for Cloudflare Tunnel

## Why

Cloudflare Tunnel, reverse proxies, and edge network gateways enforce a 300-second idle timeout on inactive WebSocket connections. Transport-level control frames (WebSocket Ping/Pong frames) do not reset the proxy's application-level data stream activity timer on many Cloudflare configurations. Consequently, idle Buzz channels and DM listeners suffer spurious disconnections and reconnection storms every 5 minutes (#112049). An application-level data frame keepalive is required to maintain long-lived WebSocket sessions transparently.

## What Changes

- Add configurable application keepalive interval (`ws_app_keepalive_interval`, default 60.0s, floor 10.0s) to `BuzzAdapter`.
- Implement background task `_ws_keepalive_loop(websocket)` spawned inside `_websocket_loop`:
  - Periodically emits a lightweight Nostr `REQ` query frame: `["REQ", "hermes-buzz-keepalive", {"kinds": [0], "limit": 0}]`.
  - Briefly pauses (0.5s) and closes the subscription: `["CLOSE", "hermes-buzz-keepalive"]`.
- Update `_handle_ws_message` in `plugins/platforms/buzz/adapter.py`:
  - Ignore `CLOSED` messages for `hermes-buzz-keepalive` to prevent false subscription-drop handling.
  - Safely ignore `EOSE` and `OK` message verbs returned by Nostr relays.
- Add regression tests in `tests/gateway/test_buzz_websocket.py` validating that the keepalive loop emits expected frames and message handlers do not treat keepalive closures as connection drops.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `buzz-websocket`: Extends WebSocket connection maintenance requirements to mandate application-level keepalive frame emission.

## Impact

- Zero disruption to existing message dispatch, event parsing, or command handling.
- Completely isolated to `plugins/platforms/buzz/adapter.py` and its test suite.
