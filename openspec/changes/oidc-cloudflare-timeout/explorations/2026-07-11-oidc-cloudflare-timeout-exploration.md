# Exploration: OIDC Cloudflare Session Timeout Discrepancy

## Context & Problem Statement
When using self-hosted OpenID Connect (`SelfHostedOIDCProvider`) with Cloudflare Zero Trust / Cloudflare Access as the Identity Provider (IdP) for `hermes dashboard`, users experience frequent authentication timeouts and re-login redirects. This occurs even when the Cloudflare Application session timeout is explicitly configured to **24 hours** in the Cloudflare Zero Trust dashboard.

## Root Cause Analysis

### 1. Missing `offline_access` Scope in Default Configuration (`SelfHostedOIDCProvider`)
In `plugins/dashboard_auth/self_hosted/__init__.py`, the default requested scopes are defined as:
```python
_DEFAULT_SCOPES = "openid profile email"
```
**Why this causes timeouts:**
- By OIDC / RFC 6749 standards, an IdP only issues a **Refresh Token** (`refresh_token`) when the client explicitly requests the **`offline_access`** scope (or when offline access is toggled on per-client).
- Without `offline_access`, Cloudflare Zero Trust `/token` endpoint issues only an `access_token` and an `id_token` (`payload.get("refresh_token")` is `None`).
- Consequently, `Session.refresh_token` is empty (`""`), and the `hermes_session_rt` cookie is never set.

### 2. ID Token Expiration vs. Cloudflare Application Session Timeout
- The `SelfHostedOIDCProvider` authenticates requests by verifying the **ID Token (`id_token`)** (`_verify_id_token()`) and sets `expires_at = int(claims["exp"])`.
- Cloudflare Access issues ID tokens with a relatively short cryptographic expiration (`exp`), typically **15 minutes to 1 hour**.
- The **24-hour Cloudflare Application session timeout** configured in Cloudflare Zero Trust controls the lifetime of the user's *Cloudflare edge session cookie* (`CF_Authorization` / `CF_AppSession`) between the user's browser and Cloudflare's authentication portal—it does **not** extend the cryptographic `exp` timestamp inside the signed `id_token` issued to `hermes dashboard`.
- Once the `id_token` expires (e.g., after 15 minutes), `verify_session()` raises `ExpiredSignatureError` and returns `None`.

### 3. Middleware Expiry Handling & Auto-SSO Bypass Gap (`gated_auth_middleware`)
In `hermes_cli/dashboard_auth/middleware.py`, `gated_auth_middleware` checks token validity:
```python
at, _rt = read_session_cookies(request)
if not at and not _rt:
    auto = _auto_sso_response(request)
    if auto is not None:
        return auto
    return _unauth_response(request, reason="no_cookie")

if at:
    for provider in list_session_providers():
        session = provider.verify_session(access_token=at)
        if session is not None:
            break
```
When `at` is present (because the browser sends `hermes_session_at` until its cookie `Max-Age` expires) but cryptographically expired (`_verify_id_token` fails $\rightarrow$ `session = None`):
1. `_attempt_refresh(request, refresh_token=_rt)` is invoked. Because `_rt` is empty (due to missing `offline_access`), refresh fails immediately (`returns None`).
2. The middleware calls `_unauth_response(request, reason="invalid_or_expired_session")` and clears session cookies, sending a **`302 Redirect` to `/login?next=...`**.
3. **The Gap:** Unlike the `not at and not _rt` branch at the top of the middleware, the expired `at` branch **never attempts `_auto_sso_response(request)`**!
4. The user is redirected to `/login`, where they must manually initiate login again, even though their 24-hour Cloudflare edge session is completely active and would have silently re-authorized them (`302 -> /auth/login -> Cloudflare -> 302 back with new code -> instant login`).

## Proposed Solution & Requirements

To completely eliminate session timeouts and honor long-lived IdP sessions without unnecessary re-login friction:

### Requirement 1: Include `offline_access` in Default OIDC Scopes
Update `_DEFAULT_SCOPES` in `plugins/dashboard_auth/self_hosted/__init__.py` to:
```python
_DEFAULT_SCOPES = "openid profile email offline_access"
```
This ensures that any conformant OIDC IdP (Cloudflare Zero Trust, Keycloak, Authentik, Auth0, Okta) that supports offline tokens will issue a `refresh_token` automatically during the initial authorization code exchange, allowing seamless background rotation when the `id_token` expires.

### Requirement 2: Engage Auto-SSO Seamless Redirect on Expired Sessions
In `hermes_cli/dashboard_auth/middleware.py` (`gated_auth_middleware`), when `at` verification fails (`session is None`) and `_attempt_refresh()` returns `None` (either because no `_rt` exists or `_rt` has expired):
- Before falling back to `_unauth_response(...)` (which sends the user to the `/login` interstitial), check if `_auto_sso_response(request)` can seamlessly initiate a silent OAuth round-trip.
- If `_auto_sso_response(request)` returns a `RedirectResponse` (meaning exactly one interactive OAuth provider is configured, the request is an HTML navigation, and no `hermes_session_sso_attempt` loop-guard cookie is present), clear the dead session cookies on that redirect response and return it directly.
- This ensures that if the local `id_token` expires and cannot be refreshed, the browser transparently bounces to Cloudflare `/oauth/authorize`. Because the user's 24-hour Cloudflare Application session is still valid at Cloudflare's edge, Cloudflare instantly issues a new authorization code and redirects right back without prompting the user or showing a login screen.

## Alternatives Considered
- **Configuring `scopes: "openid profile email offline_access"` manually in `config.yaml`**: While this works as a per-user workaround, it does not fix the out-of-the-box timeout UX when `id_token` expires and no refresh token is granted, nor does it fix the middleware gap where expired access tokens fail to trigger `_auto_sso_response`.
- **Ignoring token `exp` in `verify_session` and trusting cookie `Max-Age`**: Rejected. OIDC ID tokens are cryptographically signed identity assertions with bounded validity periods. Ignoring `exp` breaks standard JWT verification semantics and security isolation.

## Open Questions & Verification Plan
- **Verification**: Run existing unit tests (`test_dashboard_auth*.py`) and add regression tests ensuring `offline_access` is included by default and that `gated_auth_middleware` triggers `_auto_sso_response` when `verify_session` fails and refresh is unavailable.
