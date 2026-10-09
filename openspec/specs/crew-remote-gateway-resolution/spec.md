# crew-remote-gateway-resolution Specification

## Purpose
Defines the dynamic resolution of the Crew dashboard board URL based on Hermes Desktop's active connection mode (remote gateway vs local instance), along with query token and session cookie authentication support on the Hermes Web Server `/api/plugins/` routes.

## Requirements

### Requirement: Dynamic Gateway Board URL Resolution in Desktop
The Hermes Desktop Crew plugin SHALL dynamically resolve the board URL using the active connection descriptor rather than a hardcoded loopback address.

#### Scenario: Remote gateway connection
- **GIVEN** Hermes Desktop is connected to a remote gateway instance with `mode: 'remote'`
- **WHEN** the user navigates to the Crew board view
- **THEN** the iframe `src` is dynamically constructed as `${baseUrl}/api/plugins/crew/board?token=${token}` pointing to the remote gateway.

#### Scenario: Local connection fallback
- **GIVEN** Hermes Desktop is connected to a local instance with `mode: 'local'` or no remote descriptor
- **WHEN** the user navigates to the Crew board view
- **THEN** the iframe `src` resolves to `http://127.0.0.1:8799/` with fallback to local proxy.

### Requirement: Query Token and Cookie Authentication for Plugin Endpoints
The Hermes Web Server authentication middleware SHALL accept valid session tokens supplied via URL query parameters and cookies for all `/api/plugins/` routes.

#### Scenario: Iframe navigation with query token
- **GIVEN** an unauthenticated iframe requests `/api/plugins/crew/board?token=<valid_session_token>`
- **WHEN** the request is evaluated by `auth_middleware`
- **THEN** the request is accepted with HTTP 200 rather than rejected with HTTP 401.

#### Scenario: Asynchronous polling with session cookie
- **GIVEN** an iframe has loaded `/api/plugins/crew/board` and received a `hermes_session` cookie
- **WHEN** the page subsequently issues `fetch("board.json")` without custom headers
- **THEN** the request carries the cookie and passes authentication verification.
