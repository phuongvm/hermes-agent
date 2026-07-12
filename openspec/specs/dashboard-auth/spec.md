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

