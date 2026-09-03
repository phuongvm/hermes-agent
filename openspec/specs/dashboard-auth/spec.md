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


