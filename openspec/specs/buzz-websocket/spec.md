# buzz-websocket Specification

## Purpose
TBD - created by archiving change fix-buzz-websocket-idle-watchdog. Update Purpose after archive.
## Requirements
### Requirement: Idle Channel Must Not Sever Alive WebSocket Connection
The Buzz platform WebSocket reader loop MUST NOT disconnect or treat an active WebSocket connection as silent solely because no data frames have arrived within `_WS_READ_IDLE_TIMEOUT`, provided the underlying connection responds to keepalive probes.

#### Scenario: Silent channel with responsive WebSocket server
- **GIVEN** an active, authenticated Buzz WebSocket subscription
- **WHEN** no Nostr chat data frames are received from the relay for longer than `_WS_READ_IDLE_TIMEOUT` (300 seconds)
- **AND** the relay responds to WebSocket `PING` frames with `PONG`
- **THEN** the adapter MUST issue a keepalive ping probe rather than raising an immediate `ConnectionError`
- **AND** upon successful pong receipt, the adapter MUST remain connected, reset the idle wait, and continue listening
- **AND** the adapter MUST NOT log disconnection warnings or re-run NIP-42 authentication.

#### Scenario: Dead or half-open socket failing keepalive probe
- **GIVEN** an active Buzz WebSocket connection that has become dead or stuck in `CLOSE_WAIT`
- **WHEN** no data frames are received for `_WS_READ_IDLE_TIMEOUT`
- **AND** the keepalive ping probe times out or raises an error
- **THEN** the adapter MUST raise `ConnectionError`
- **AND** the loop MUST enter the reconnect backoff path to re-establish the connection.

### Requirement: Application-Level Keepalive Emission
The `BuzzAdapter` SHALL run an asynchronous background keepalive loop for the lifetime of each active WebSocket connection that periodically emits application-level data frames to maintain edge proxy and tunnel session state.

#### Scenario: Periodic REQ and CLOSE frame emission
- **WHEN** an active WebSocket connection is maintained in `_websocket_loop`
- **THEN** `_ws_keepalive_loop` SHALL emit a Nostr `REQ` frame with subscription ID `hermes-buzz-keepalive` and filter `{"kinds": [0], "limit": 0}` every `ws_app_keepalive_interval` seconds
- **AND** it SHALL emit a corresponding `CLOSE` frame for subscription ID `hermes-buzz-keepalive` after a brief delay

#### Scenario: Relay close of keepalive subscription does not drop channels
- **WHEN** the relay sends a `CLOSED` frame for subscription ID `hermes-buzz-keepalive`
- **THEN** the adapter SHALL ignore the message
- **AND** it SHALL NOT mark any active channel or DM subscription as restricted or closed

#### Scenario: Configurable keepalive interval with safety floor
- **WHEN** `ws_app_keepalive_interval` is configured via environment or adapter configuration
- **THEN** the interval SHALL enforce a minimum floor of `10.0` seconds
- **AND** default to `60.0` seconds when omitted or invalid


