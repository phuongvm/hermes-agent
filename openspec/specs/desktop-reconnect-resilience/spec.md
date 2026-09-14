# desktop-reconnect-resilience Specification

## Purpose
TBD - created by archiving change desktop-reconnect-session-resilience. Update Purpose after archive.
## Requirements
### Requirement: Desktop Background Sync Suppression During Gateway Disconnect
The Desktop renderer SHALL suppress all background sync operations (`refreshProfiles`, `refreshSessions`, `refreshHermesConfig`, `refreshCurrentModel`) while the gateway connection state is not `'open'`. Suppressed calls SHALL be deferred and coalesced into a single refresh burst when the connection transitions back to `'open'`.

#### Scenario: Background sync deferred during reconnect window
- **WHEN** the gateway connection state transitions from `'open'` to `'connecting'` or `'disconnected'`
- **AND** background sync hooks attempt to fire `refreshProfiles()` or `refreshSessions()`
- **THEN** the hooks SHALL NOT issue any HTTP/IPC requests to the backend
- **AND** no timeout error pop-ups SHALL be displayed to the user

#### Scenario: Coalesced refresh on reconnect completion
- **WHEN** the gateway connection state transitions from `'connecting'` back to `'open'`
- **THEN** the renderer SHALL issue exactly one refresh call per data source (profiles, sessions, config, model)
- **AND** the refresh SHALL use the latest cached state as the baseline

#### Scenario: Window focus during disconnect does not trigger refresh
- **WHEN** the user focuses the Desktop window while the gateway is in `'connecting'` state
- **THEN** the profile rail refresh hook SHALL NOT issue a refresh request
- **AND** no IPC timeout errors SHALL be generated

### Requirement: Coalesced Error Absorption in Profile Store During Reconnect
The Desktop profile store SHALL silently absorb network errors that occur while the gateway connection state is not `'open'`, preserving cached profile data in the UI instead of surfacing uncaught promise rejections.

#### Scenario: Network error during reconnect preserved as cached state
- **WHEN** `refreshProfiles()` fails with a network error while gateway state is `'connecting'`
- **THEN** the profile store SHALL retain the previously loaded profile list
- **AND** no error toast or pop-up SHALL be displayed to the user

#### Scenario: Network error during stable connection surfaces normally
- **WHEN** `refreshProfiles()` fails with a network error while gateway state is `'open'`
- **THEN** the error SHALL be surfaced to the user through the normal error handling path

### Requirement: Debounced Reauth Latch with Transient Drop Classification
The Electron main process SHALL classify 401 HTTP responses into Hard Auth Rejection and Transient Gateway Drop categories. The `isReauthRequired` flag SHALL only be set after ≥2 consecutive 401 failures within a 15-second window from the same backend endpoint when the gateway was previously in a connected state.

#### Scenario: Single transient 401 during reconnect does not trigger reauth
- **WHEN** the Electron main process receives a single 401 response from a backend health probe
- **AND** the gateway connection state is `'connecting'` or `'disconnected'`
- **THEN** the `isReauthRequired` flag SHALL NOT be set
- **AND** no browser loopback OAuth window SHALL be opened
- **AND** the error SHALL be treated as a connectivity error with automatic retry

#### Scenario: Consecutive 401s from stable connection trigger reauth
- **WHEN** the Electron main process receives ≥2 consecutive 401 responses within 15 seconds
- **AND** the gateway was previously in `'open'` state before the first 401
- **THEN** the `isReauthRequired` flag SHALL be set
- **AND** the browser loopback OAuth flow SHALL be initiated

#### Scenario: Mixed 401 and success responses reset the consecutive counter
- **WHEN** the Electron main process receives a 401 response followed by a 200 response
- **THEN** the consecutive 401 counter SHALL be reset to zero
- **AND** the `isReauthRequired` flag SHALL NOT be set

### Requirement: Single Global Reauth Modal Across Pooled Connections
The Desktop app SHALL display at most one re-authentication modal or notification at any given time, regardless of how many pooled remote backend connections have encountered auth failures simultaneously.

#### Scenario: Multiple pooled connections fail with 401 simultaneously
- **WHEN** pooled remote backends `conn:host::profile-a` and `conn:host::profile-b` both trigger `isReauthRequired`
- **THEN** exactly one re-auth modal SHALL be displayed to the user
- **AND** the successful re-authentication SHALL resolve the auth state for all pooled connections that triggered the reauth

#### Scenario: Second reauth trigger coalesced into active modal
- **WHEN** a re-auth modal is already displayed for one connection
- **AND** another pooled connection triggers `isReauthRequired`
- **THEN** no additional modal SHALL be displayed
- **AND** the second connection SHALL wait for the outcome of the active modal

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

