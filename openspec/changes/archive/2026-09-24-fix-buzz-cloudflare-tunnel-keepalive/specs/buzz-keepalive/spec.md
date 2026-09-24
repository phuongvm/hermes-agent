# Delta Specification: Buzz WebSocket Application Keepalive

## ADDED Requirements

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
