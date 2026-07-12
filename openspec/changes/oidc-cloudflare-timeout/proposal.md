## Why

When self-hosting `hermes dashboard` behind Cloudflare Zero Trust / Cloudflare Access with OpenID Connect (`SelfHostedOIDCProvider`), users experience frequent session expiration (`401 Unauthorized` or re-login redirects after ~15 minutes) even when the Cloudflare Application session timeout is configured to 24 hours. This happens because Cloudflare Access app tokens (`id_token`) are short-lived (typically 15 minutes) and do not support standard OIDC token refresh flows (`/token` endpoint refresh), even when `offline_access` is requested.

Furthermore, when the short-lived ID token expires, API requests (`/api/*`) return `401 Unauthorized` causing the SPA to kick the user back to `/login`, because `_auto_sso_response` only handles HTML document navigations and cannot transparently refresh token cookies during API calls.

## What Changes

- Update `SelfHostedOIDCProvider` (`plugins/dashboard_auth/self_hosted/__init__.py`) to implement a configurable 24-hour Session TTL (`_session_ttl_seconds = 86400`) from the initial authentication time (`iat`).
- Update `_verify_id_token(id_token, allow_expired_within_ttl=True)`: when `allow_expired_within_ttl=True`, it verifies the cryptographic signature (RSA/EC), `aud`, `iss`, and `sub` claims with PyJWT `verify_exp=False`, and enforces `time.time() < iat + session_ttl_seconds`.
- Update `_session_from_tokens` to set `expires_at = max(exp_claim, iat_claim + session_ttl)` for identity proxies and self-hosted OIDC providers that do not support standard refresh flows (`refresh_token` is empty or short-lived ID tokens are issued without working refresh endpoints).
- Update `gated_auth_middleware` (`hermes_cli/dashboard_auth/middleware.py`) inside the `if session is None:` branch to check `_auto_sso_response(request)` for HTML document navigations when `_attempt_refresh` returns `None`.
- Set `_DEFAULT_SCOPES = "openid profile email offline_access"` as a defensive default for standard OIDC providers that support refresh tokens.

## Capabilities

### New Capabilities
- `dashboard-auth`: Self-hosted OpenID Connect transparently supports 24-hour sessions (`SESSIONS_TTL_SECONDS`) for short-lived ID tokens from identity proxies (e.g. Cloudflare Access) via secure `iat` + TTL cryptographic verification without requiring standard `/token` refresh endpoints.

### Modified Capabilities
- `dashboard-auth`: `gated_auth_middleware` transparently initiates auto-SSO round-trips for expired HTML document sessions before surfacing unauthenticated redirects.

## Impact

- **Affected Code**:
  - `plugins/dashboard_auth/self_hosted/__init__.py` (`SelfHostedOIDCProvider.__init__`, `_verify_id_token`, `_session_from_tokens`, `verify_session`)
  - `hermes_cli/dashboard_auth/middleware.py` (`gated_auth_middleware`)
- **Dependencies**: No new external dependencies. Uses standard PyJWT options (`verify_exp: False`).
- **System Behavior**: Users authenticating via Cloudflare Zero Trust or other short-lived OIDC proxies maintain unbroken 24-hour dashboard sessions across both HTML navigation and SPA API calls (`/api/*`) without 15-minute timeouts or re-login kicks.
