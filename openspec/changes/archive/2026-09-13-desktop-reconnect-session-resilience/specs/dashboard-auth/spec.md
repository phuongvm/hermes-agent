## ADDED Requirements

### Requirement: Persistent Dashboard Session Token Resolution
The dashboard server SHALL resolve the session token using a durable on-disk fallback when the `HERMES_DASHBOARD_SESSION_TOKEN` environment variable is not set. The server SHALL read from and write to a persistent token file at `$HERMES_HOME/.dashboard_session_token` (file permissions restricted to the service user), ensuring that daemon restarts preserve the active session token.

#### Scenario: First boot without env var creates persistent token file
- **WHEN** the dashboard server starts for the first time
- **AND** `HERMES_DASHBOARD_SESSION_TOKEN` is not set in the environment
- **AND** no `.dashboard_session_token` file exists at `$HERMES_HOME/`
- **THEN** the server SHALL generate a cryptographically random token
- **AND** write it to `$HERMES_HOME/.dashboard_session_token` with restrictive file permissions (0600 on Unix)
- **AND** use the written token as `_SESSION_TOKEN` for the session

#### Scenario: Subsequent boot reads existing persistent token
- **WHEN** the dashboard server restarts
- **AND** `HERMES_DASHBOARD_SESSION_TOKEN` is not set in the environment
- **AND** a valid `.dashboard_session_token` file exists at `$HERMES_HOME/`
- **THEN** the server SHALL read the token from the file
- **AND** use the file-sourced token as `_SESSION_TOKEN`
- **AND** previously connected Desktop clients presenting the same token SHALL NOT receive 401 errors

#### Scenario: Environment variable takes precedence over file
- **WHEN** the dashboard server starts
- **AND** `HERMES_DASHBOARD_SESSION_TOKEN` is set in the environment
- **THEN** the server SHALL use the environment variable value as `_SESSION_TOKEN`
- **AND** the `.dashboard_session_token` file SHALL NOT be read or modified

### Requirement: Startup Grace Period with 503 Response
The dashboard auth middleware SHALL return HTTP 503 (`Service Unavailable`) with a `Retry-After` header during the server initialization window instead of HTTP 401 (`session_expired`) for requests arriving before routes and plugins are fully loaded.

#### Scenario: Request during server initialization receives 503
- **WHEN** a Desktop client sends an authenticated request to the dashboard
- **AND** the server has started but has not yet completed route and plugin initialization
- **THEN** the auth middleware SHALL respond with HTTP 503
- **AND** the response SHALL include a `Retry-After: 3` header
- **AND** the response SHALL NOT include `session_expired` or `unauthenticated` error codes

#### Scenario: Request after initialization completes receives normal auth response
- **WHEN** the server has completed route and plugin initialization
- **AND** a Desktop client sends a request with a valid session token
- **THEN** the auth middleware SHALL process the request normally (200 for valid token, 401 for genuinely invalid token)

#### Scenario: Grace period does not bypass auth for invalid tokens after init
- **WHEN** the server has completed initialization
- **AND** a client sends a request with a genuinely revoked or invalid token
- **THEN** the auth middleware SHALL return HTTP 401 as normal
- **AND** the grace period logic SHALL NOT interfere with standard auth validation
