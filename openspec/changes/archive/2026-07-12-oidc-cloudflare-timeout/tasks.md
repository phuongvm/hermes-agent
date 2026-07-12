## 1. Cryptographic Session TTL Extension (`SelfHostedOIDCProvider`)

- [x] 1.1 Implement `_session_ttl_seconds = 86400` (24 hours) configuration in `SelfHostedOIDCProvider.__init__` (`plugins/dashboard_auth/self_hosted/__init__.py`)
- [x] 1.2 Update `_verify_id_token(id_token, allow_expired_within_ttl=True)` to decode with PyJWT `verify_exp=False` when `allow_expired_within_ttl=True` while checking signature, `aud`, `iss`, and `sub`, and enforcing `time.time() < iat + session_ttl`
- [x] 1.3 Update `verify_session(access_token=at)` to pass `allow_expired_within_ttl=True` to `_verify_id_token`
- [x] 1.4 Update `_session_from_tokens` to set `Session.expires_at = max(exp_claim, iat_claim + session_ttl)` when `refresh_token` is empty or refresh flows are unsupported

## 2. Provider Scopes Configuration

- [x] 2.1 Update `_DEFAULT_SCOPES = "openid profile email offline_access"` in `plugins/dashboard_auth/self_hosted/__init__.py`
- [x] 2.2 Verify `SelfHostedOIDCProvider.__init__` uses the updated default scopes when `scopes` parameter is not overridden

## 3. Middleware Transparent Session Recovery

- [x] 3.1 Update `gated_auth_middleware` in `hermes_cli/dashboard_auth/middleware.py` inside the `if session is None:` branch to check `_auto_sso_response(request)` when token refresh (`_attempt_refresh`) returns `None`
- [x] 3.2 Ensure dead/stale session cookies (`hermes_session_at`, `hermes_session_rt`) are cleared on the `_auto_sso_response` redirect response before returning it

## 4. Verification & Testing

- [x] 4.1 Run unit and integration tests for dashboard auth (`tests/hermes_cli/test_dashboard_auth_middleware.py`, `tests/plugins/dashboard_auth/test_self_hosted_provider.py`) to verify `test_expired_id_token_within_session_ttl_returns_session`, `test_expired_beyond_session_ttl_returns_none`, and `test_invalid_cookie_redirects_on_html`
- [x] 4.2 Verify live E2E session stability in browser across 15+ minutes with Cloudflare Access OIDC
