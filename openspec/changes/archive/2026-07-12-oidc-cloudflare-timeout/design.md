## Context

`SelfHostedOIDCProvider` authenticates users against third-party or self-hosted OpenID Connect providers. When integrating with zero-trust edge proxies like Cloudflare Access / Cloudflare Zero Trust, the IdP returns an ID token (`id_token`) with a very short expiration timestamp (`exp`, often 15 minutes) to enforce regular re-validation at the edge.

Because Cloudflare Access app tokens function as both authentication identity and edge access tokens, Cloudflare does not support standard OIDC `/token` refresh requests (`refresh_token` is either omitted or returns `400 Bad Request` during `_exchange`). When `verify_session(access_token=at)` strict `exp` checks fail after 15 minutes, two critical problems occur:
1. **API Requests (`/api/*`)**: SPA requests fail with `401 Unauthorized`, causing the frontend router to redirect the user to `/login?next=...` mid-session.
2. **HTML Navigations**: `gated_auth_middleware` attempts `_attempt_refresh`, gets `None`, and falls straight through to `_unauth_response`, clearing session cookies and forcing the `/login` interstitial.

## Goals / Non-Goals

**Goals:**
- Enable robust 24-hour (`SESSIONS_TTL_SECONDS`) session persistence for self-hosted OIDC deployments behind short-lived identity proxies (Cloudflare Access) without relying on unsupported `/token` refresh endpoints.
- Ensure cryptographic integrity: verify RSA/EC signatures, `aud`, `iss`, and `sub` against IdP JWKS even when allowing tokens beyond their short `exp` claim within the application session TTL.
- Provide seamless `_auto_sso_response(request)` recovery in `gated_auth_middleware` when an HTML document session is genuinely expired or unauthenticated.
- Preserve exact existing security invariants, including the one-shot `hermes_session_sso_attempt` loop guard to prevent infinite redirect loops when edge sessions are dead.

**Non-Goals:**
- Modifying the token lifetime (`exp`) inside signed JWTs issued by third-party IdPs.
- Changing how the bundled `nous` auth provider operates or how API (`/api/*`) unauthenticated responses (`401 JSON`) behave when sessions exceed the 24-hour TTL.

## Decisions

### Decision 1: Cryptographic Session TTL Extension in `SelfHostedOIDCProvider`
- **Choice**: Introduce `_session_ttl_seconds` (default 86400s / 24 hours) to `SelfHostedOIDCProvider`. Update `_verify_id_token(id_token, allow_expired_within_ttl=True)` to decode with PyJWT option `{"verify_exp": not allow_expired_within_ttl}`, while maintaining strict validation of `aud`, `iss`, `sub`, and cryptographic signatures (`signing_key.key`). When `allow_expired_within_ttl=True`, enforce `time.time() < iat + session_ttl`. Update `_session_from_tokens` to set `expires_at = max(exp_claim, iat_claim + session_ttl)` when refresh flows are unsupported (`refresh_token` empty or proxy-bound ID token).
- **Rationale**: For edge identity proxies like Cloudflare Access, the browser's access to the application is already protected by Cloudflare's edge cookies (`CF_Authorization`). The backend ID token (`id_token`) represents the authenticated identity at login time (`iat`). Validating the JWT signature and ensuring `time.time() < iat + 24h` guarantees identity authenticity while preventing 15-minute `401 Unauthorized` API kickouts.
- **Alternatives Considered**: Relying solely on `_attempt_refresh` or `_auto_sso_response`. Rejected because `_auto_sso_response` cannot intercept asynchronous SPA `fetch()` / XHR API calls (`/api/*`), resulting in 401s and forced UI re-logins every 15 minutes.

### Decision 2: Engage `_auto_sso_response` in `gated_auth_middleware` on Expired HTML Sessions
- **Choice**: Inside `gated_auth_middleware`, in the branch where `at` is present but `session is None` (both `verify_session` and `_attempt_refresh` returned `None`), check `_auto_sso_response(request)` for HTML document requests:
  ```python
  if session is None:
      auto = _auto_sso_response(request)
      if auto is not None:
          from hermes_cli.dashboard_auth.cookies import clear_session_cookies
          from hermes_cli.dashboard_auth.prefix import prefix_from_request
          clear_session_cookies(auto, prefix=prefix_from_request(request))
          return auto
      # Fall through to _unauth_response(...) and cookie clear if auto is None
  ```
- **Rationale**: If a session has exceeded the 24-hour TTL (`verify_session` returns `None`), checking `_auto_sso_response(request)` on HTML navigations allows the middleware to transparently bounce to `/auth/login?provider=self-hosted`. If the user still holds an active 24-hour Cloudflare edge session, Cloudflare instantly completes the authorization code flow without prompting, returning a fresh ID token and seamlessly starting a new 24-hour session.

### Decision 3: Add `offline_access` to `_DEFAULT_SCOPES`
- **Choice**: Change `_DEFAULT_SCOPES = "openid profile email"` to `_DEFAULT_SCOPES = "openid profile email offline_access"`.
- **Rationale**: Standard OIDC providers (Authentik, Keycloak, Auth0) require `offline_access` to issue refresh tokens. While Cloudflare Access omits or does not support OIDC refresh endpoints, standard providers benefit from automatic rotation, and the Session TTL Extension handles short-lived proxies where refresh is unavailable.

## Risks / Trade-offs

- **[Risk] Revoked Cloudflare user session within the 24-hour TTL window.**
  - **Mitigation**: If a user's Cloudflare Access session is revoked at the edge, Cloudflare Zero Trust blocks their incoming HTTP requests at the CDN/proxy layer (`CF_Authorization` verification fails at Cloudflare before reaching Hermes Gateway). Thus, the application-level 24-hour TTL in Hermes does not introduce unauthorized access risk behind Cloudflare.
- **[Risk] Infinite redirect loop between dashboard and Cloudflare if IdP session is dead or misconfigured.**
  - **Mitigation**: `_auto_sso_response` sets a short-lived `hermes_session_sso_attempt` cookie when initiating a redirect. If the browser bounces through `/auth/login` and returns without a valid token, `read_sso_attempt_cookie(request)` detects the marker, aborts auto-SSO, clears the cookie, and serves the normal `/login` interstitial (`_unauth_response`).
