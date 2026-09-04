# Delta Specification: Buzz WebSocket Idle Watchdog

## ADDED REQUIREMENTS

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
