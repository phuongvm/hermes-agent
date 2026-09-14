# Delta Specification: Terminal Auth Rejection Suspension and Recovery

## ADDED Requirements

### Requirement: Terminal Auth Rejection State Transition and Network Suppression
The Desktop application SHALL atomically transition a normalized remote base URL into a signed-out and re-authentication required state upon receiving an authoritative terminal refresh rejection. Background network polling, profile rail refreshes, and project tree self-healing retries for that base URL SHALL be suspended until a confirmed sign-in restores the authenticated session.

#### Scenario: Authoritative terminal refresh rejection atomically enters signed-out state and stops repeated network calls
- **WHEN** a Desktop client receives an authoritative terminal 401 rejection from `/auth/native/refresh` for a normalized base URL
- **THEN** the Desktop application MUST atomically mark that base URL as `reauth-required` and `signed-out`
- **AND** obsolete credentials matching that base URL MUST be cleared
- **AND** background profile refreshes, profile rail active refreshes, and project tree self-healing retries for that base URL MUST be suppressed without issuing network requests, retry timers, or uncaught promise rejections

#### Scenario: Stale in-flight rejection cannot clear credentials written by a newer successful login
- **WHEN** a request sent with an earlier session's bearer receives an authoritative 401 rejection after a newer successful login has already stored fresh credentials for that base URL
- **THEN** the token refresher MUST NOT clear or overwrite the newly stored credentials
- **AND** the active connection MUST remain authenticated with the newer credentials

#### Scenario: Confirmed sign-in clears terminal state and resumes coalesced background work once
- **WHEN** a user completes a successful authentication flow for a base URL previously in the `reauth-required` / `signed-out` state
- **THEN** the Desktop application MUST clear the `reauth-required` and `signed-out` state for that base URL
- **AND** suspended background synchronization operations (profiles, config, project tree) MUST resume and coalesce into a single refresh execution
