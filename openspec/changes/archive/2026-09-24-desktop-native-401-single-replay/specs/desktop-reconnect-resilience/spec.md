# Delta Specification: Desktop Native Token 401 Single-Replay

## ADDED REQUIREMENTS

### Requirement: Native Access Token Force-Refresh and Single-Replay on Authoritative 401
The Electron main process SHALL intercept authoritative 401 responses for requests issued with native bearer tokens, perform at most ONE token refresh for that gateway, and replay the request once with the rotated bearer token before escalating or propagating 401.

#### Scenario: Authoritative 401 triggers single forced refresh and replay
- **WHEN** a request issued with a native bearer token fails with an authoritative HTTP 401 response
- **THEN** the refresher SHALL bypass local `expiresAt` and refresh tokens via `refreshToken`
- **AND** the request SHALL be replayed once with the newly rotated access token
- **AND** if the replayed request also fails with 401, the 401 SHALL be propagated without further refreshes

#### Scenario: Staggered 401s for identical rejected bearer coalesce into single token rotation
- **WHEN** two concurrent requests are issued with the same initial bearer token
- **AND** the second request's 401 arrives after the first request's forced refresh has already published to storage
- **THEN** the second request SHALL reuse the newly stored bearer without triggering a second token refresh
- **AND** both requests SHALL replay with the same rotated bearer token

#### Scenario: Stale 401 after newer login/session replacement does not rotate or clear session
- **WHEN** a request sent with a previous session's bearer receives an authoritative 401 after a newer session has been stored
- **THEN** the refresher SHALL NOT invoke `refresh` or clear credentials
- **AND** the newer session credentials SHALL remain intact
