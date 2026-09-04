# Change: Fix Multi-Provider Dashboard Auth Foreign KID JWKS Lookup Failure

## Why
When Hermes Agent is configured with multiple dashboard auth providers (`basic`, `nous`, `oidc`/`self_hosted`), an inbound request presenting an OIDC Bearer JWT or cookie is first evaluated by the `nous` provider.
The `nous` provider calls `PyJWKClient.get_signing_key_from_jwt()`, which connects to Nous Portal JWKS successfully. Since the token was issued by an OIDC provider, its `kid` is not present in Nous Portal's signing keys. PyJWT raises `jwt.exceptions.PyJWKClientError('Unable to find a signing key that matches: "<kid>"')`.

Commit `2f44998353` (#94558) introduced `classify_jwks_lookup_error` in `hermes_cli/dashboard_auth/base.py`, expecting foreign keys to raise `jwt.PyJWKSetError`. However, `PyJWKSetError` is only raised when the JWKS payload has no keys at all (`{"keys": []}`). When the JWKS contains keys but none match the `kid`, PyJWT raises `PyJWKClientError`.

As a result, `classify_jwks_lookup_error` misclassifies `PyJWKClientError` as `ProviderError` ("provider unreachable"), causing:
1. False-positive warning logs: `dashboard-auth: provider 'nous' unreachable during bearer verify: JWKS lookup failed: ...`.
2. Middleware flagging `nous` as unreachable, which can convert an invalid/expired token response into HTTP 503 rather than HTTP 401.

## What Changes
- Update `classify_jwks_lookup_error` in `hermes_cli/dashboard_auth/base.py` to recognize `PyJWKClientError` containing `"Unable to find a signing key that matches"` as `InvalidCodeError` ("token not verifiable by this provider"), allowing the provider loop to gracefully proceed to subsequent providers (e.g. `self_hosted`).
- Add regression tests in `tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py` with a mock JWKS containing valid signing keys (non-empty) and a JWT bearing an unknown `kid`, verifying that:
  - `verify_session()` returns `None` without raising `ProviderError`.
  - HTTP middleware returns 401 and does not log "unreachable".
