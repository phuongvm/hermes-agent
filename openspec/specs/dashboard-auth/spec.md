# dashboard-auth Specification

## Purpose
TBD - created by archiving change oidc-cloudflare-timeout. Update Purpose after archive.
## Requirements
### Requirement: Self-Hosted OIDC Cryptographic Session TTL Extension
The `SelfHostedOIDCProvider` MUST support a configurable session TTL (`_session_ttl_seconds`, default 86400 seconds) from the token's issued-at time (`iat`) to maintain long-lived sessions across short-lived ID tokens issued by identity proxies where standard refresh flows are unavailable.

#### Scenario: Verify ID token allows expiration within session TTL
- **WHEN** `_verify_id_token(id_token, allow_expired_within_ttl=True)` is called with an ID token whose `exp` claim is in the past but whose `iat + _session_ttl_seconds` is in the future
- **THEN** `_verify_id_token` MUST verify the cryptographic signature against the IdP JWKS and check `aud`, `iss`, and `sub` claims without raising an `InvalidCodeError` due to `exp`

#### Scenario: Verify ID token rejects tokens beyond session TTL
- **WHEN** `_verify_id_token(id_token, allow_expired_within_ttl=True)` is called with an ID token where `time.time() >= iat + _session_ttl_seconds`
- **THEN** `_verify_id_token` MUST raise an `InvalidCodeError`

#### Scenario: Session from tokens extends expiry for unsupported refresh flows
- **WHEN** `_session_from_tokens` maps verified OIDC claims where `refresh_token` is empty or standard refresh flows are unsupported
- **THEN** `Session.expires_at` MUST be set to `max(exp_claim, iat_claim + _session_ttl_seconds)`

### Requirement: Self-Hosted OIDC Offline Access Scope
The `SelfHostedOIDCProvider` MUST include `offline_access` by default in its requested scopes when initiating OpenID Connect authorization code requests.

#### Scenario: Default scopes request offline_access
- **WHEN** `SelfHostedOIDCProvider` is initialized with default scopes and begins a login flow (`start_login`)
- **THEN** the `scope` parameter in the `authorization_endpoint` URL MUST equal `openid profile email offline_access`

#### Scenario: User custom scopes override default
- **WHEN** a user explicitly configures `dashboard.oauth.self_hosted.scopes` or `HERMES_DASHBOARD_OIDC_SCOPES` to a custom string
- **THEN** `SelfHostedOIDCProvider` MUST use the exact custom scopes string provided by the user without appending defaults

### Requirement: Middleware Transparent Recovery on Expired ID Token
The `gated_auth_middleware` MUST attempt a transparent `_auto_sso_response` round-trip when an access/ID token is cryptographically expired (`verify_session()` returns `None`) and token refresh is unavailable (`_attempt_refresh()` returns `None`).

#### Scenario: Expired access token with no refresh token initiates auto-SSO
- **WHEN** a document navigation request arrives with an expired `hermes_session_at` cookie (`verify_session()` returns `None`) and `_attempt_refresh()` returns `None`
- **THEN** `gated_auth_middleware` MUST invoke `_auto_sso_response(request)` before falling through to `_unauth_response()`
- **AND** if `_auto_sso_response(request)` returns a valid redirect response, `gated_auth_middleware` MUST clear stale session cookies and return the auto-SSO redirect directly

#### Scenario: Auto-SSO loop guard prevents redirect loop on expired access token
- **WHEN** an expired access token cannot be refreshed AND the `hermes_session_sso_attempt` loop-guard cookie is already present on the request
- **THEN** `_auto_sso_response(request)` MUST return `_unauth_response(request, reason="no_cookie")` with the loop-guard cookie cleared, forcing the `/login` interstitial instead of looping

### Requirement: Native Authorization Multi-Provider Chooser
The `/auth/native/authorize` route MUST render an interactive chooser page when the `provider` query parameter is omitted and more than one brokerable, non-password authentication provider is registered.

#### Scenario: Multiple OAuth providers render chooser page
- **WHEN** a client makes a `GET` request to `/auth/native/authorize` with valid PKCE parameters (`code_challenge`, `code_challenge_method=S256`, loopback `redirect_uri`, `state`) and no `provider` query parameter
- **AND** more than one brokerable session provider (`supports_password` is false or not set) is registered
- **THEN** the server MUST respond with HTTP status 200
- **AND** the response `Content-Type` MUST be `text/html; charset=utf-8`
- **AND** the response `Cache-Control` header MUST contain `no-store, no-cache, must-revalidate`
- **AND** the response body MUST contain links for each registered brokerable provider
- **AND** each link MUST point to `/auth/native/authorize` preserving the client's `code_challenge`, `code_challenge_method`, `redirect_uri`, and `state`, with `provider` set to the specific provider's name

#### Scenario: Single OAuth provider auto-selects without chooser
- **WHEN** a client makes a `GET` request to `/auth/native/authorize` with valid PKCE parameters and no `provider` query parameter
- **AND** exactly one brokerable session provider is registered
- **THEN** the server MUST redirect (HTTP 302) directly to that provider's authorization endpoint, bypassing the chooser page

#### Scenario: Selection of provider from chooser proceeds to IdP
- **WHEN** a user follows a link from the chooser page containing `?provider=<name>&code_challenge=...`
- **THEN** the server MUST register the pending broker authorization and redirect (HTTP 302) to that provider's upstream authorization URL

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

### Requirement: Host Auth Provider Process-Home Ownership
The dashboard host authentication provider and session database SHALL bind immutably to the host process home directory resolved at server startup. The provider and its session store SHALL remain stable across routed per-request profile operations, and plugin rediscovery SHALL NOT replace the active host-owned provider with a request-scoped or profile-scoped instance.

#### Scenario: Root-launched dashboard remains bound to root auth store after routed profile rediscovery
- **WHEN** a dashboard server is launched under the root process home
- **AND** a per-request profile override or profile-scoped plugin rediscovery is executed for a named profile
- **THEN** the host dashboard authentication provider MUST remain bound to the root process session store
- **AND** session refresh requests presenting valid root tokens MUST succeed
- **AND** no new session database file SHALL be created in the routed profile's directory by that rediscovery

#### Scenario: Dashboard launched explicitly under one named profile remains bound to that process profile store
- **WHEN** a dashboard server is launched explicitly with a named profile (`-p <name>`)
- **THEN** the host dashboard authentication provider MUST bind to that process's profile home session database
- **AND** authentication operations for that dashboard process SHALL operate exclusively against that profile's session store
- **AND** the dashboard process SHALL NOT be forced into the root process session store

#### Scenario: Concurrent or forced rediscovery cannot replace active host-auth provider with request-scoped instance
- **WHEN** a concurrent or forced plugin rediscovery occurs while a host-owned dashboard auth provider is active
- **AND** the rediscovery occurs within a request-scoped or profile-scoped context
- **THEN** the process-global auth provider registry MUST reject replacing the active host-owned provider with the request-scoped instance
- **AND** the active host-owned provider MUST continue servicing authentication requests

