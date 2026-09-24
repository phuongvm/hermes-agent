# Design: Buzz WebSocket Application Keepalive

## Context

`BuzzAdapter` maintains persistent WebSocket connections to Nostr relays. While transport-level `ping_interval` and `ping_timeout` are sent to `websockets.connect`, upstream edge proxies (such as Cloudflare Tunnel terminating ingress to local relay gateways) do not recognize protocol-level ping/pong as client application activity. After 300 seconds without WebSocket data frames, Cloudflare drops the connection with 1006 / reset.

## Architecture & Decisions

### D1: Nostr-Native Zero-Data Query (`kinds: [0], limit: 0`)
To reset the proxy data timer without incurring network or relay compute overhead:
- The keepalive frame queries metadata events (`kind: 0`) with `limit: 0`. Relays immediately return `EOSE` or empty event batches without database scanning.
- An immediate `CLOSE` frame follows 0.5s later.

### D2: Concurrent Lifecyle in `_websocket_loop`
`_ws_keepalive_loop` is spawned alongside `_ws_read_loop` and `_ws_discovery_loop`:
```python
tasks = {
    asyncio.create_task(self._ws_read_loop(websocket, subscriptions)),
    asyncio.create_task(self._ws_discovery_loop(websocket, subscriptions)),
    asyncio.create_task(self._ws_keepalive_loop(websocket)),
}
```
If the websocket closes or either sibling task exits, the keepalive loop cancels cleanly upon `asyncio.FIRST_COMPLETED`.

### D3: Message Handler Discrimination
In Nostr, relays respond to `REQ` with `EVENT`, `EOSE`, and `CLOSED`.
When `CLOSED` arrives with subscription ID `hermes-buzz-keepalive`, `_handle_ws_message` returns immediately, preventing the adapter from marking real channels as restricted or disconnected.
Relay ack messages (`EOSE`, `OK`) are explicitly ignored without logging warnings.
