# Design: Accurate JWKS Key Lookup Error Classification

## Context
`hermes_cli/dashboard_auth/base.py` provides `classify_jwks_lookup_error(exc: BaseException) -> Exception`, used by `NousDashboardAuthProvider._verify_jwt` and `SelfHostedOIDCProvider._verify_id_token` to translate exceptions from `PyJWKClient` into protocol-level errors:
- `InvalidCodeError`: The token is unparseable or cannot be verified by this provider (not signed by this IDP / expired / foreign kid). Signals `verify_session()` to return `None` so subsequent providers can try.
- `ProviderError`: The IDP or JWKS endpoint is unreachable or returning invalid responses (transient transport failure). Signals `503 Service Unavailable` so the user is not forcibly logged out during a transient network glitch.

## Error Behavior in PyJWT (`jwt/jwks_client.py`)
In `PyJWKClient.get_signing_key(kid)`:
```python
signing_keys = self.get_signing_keys()
signing_key = self.match_kid(signing_keys, kid)

if not signing_key:
    signing_keys = self.get_signing_keys(refresh=True)
    signing_key = self.match_kid(signing_keys, kid)

    if not signing_key:
        raise PyJWKClientError(
            f'Unable to find a signing key that matches: "{kid}"'
        )
```

1. If the JWKS endpoint is unreachable, PyJWT raises `jwt.PyJWKClientConnectionError` (which subclasses `PyJWKClientError`).
2. If the JWKS endpoint returns a malformed JSON or empty list of keys, PyJWT raises `jwt.PyJWKSetError` or `PyJWKClientError("The JWKS endpoint did not return a JSON object")`.
3. If the JWKS endpoint is reachable, healthy, and returns keys, but no key matches `kid`, PyJWT raises `jwt.PyJWKClientError('Unable to find a signing key that matches: ...')`.

## Proposed Change
In `hermes_cli/dashboard_auth/base.py`:
```python
    if isinstance(exc, jwt.PyJWKClientError):
        # A healthy JWKS was fetched, but contains no matching key for this token's kid.
        # This token was minted by another IDP (e.g. self-hosted OIDC in multi-provider mode).
        if "Unable to find a signing key that matches" in str(exc):
            return InvalidCodeError(f"token not verifiable by this provider: {exc}")
        return ProviderError(f"JWKS lookup failed: {exc}")
```

This ensures:
- Real transport failures (`PyJWKClientConnectionError`) still map to `ProviderError`.
- Structural JWKS errors still map to `ProviderError`.
- Unmatched `kid` cleanly maps to `InvalidCodeError`, fulfilling the original intent of commit `2f44998353`.
