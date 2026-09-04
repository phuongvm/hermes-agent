# Exploration: Multi-Provider Dashboard Auth - Foreign KID JWKS Lookup Failure

> **Date**: 2026-09-04  
> **Topic**: Dashboard Auth Provider 'nous' unreachable during bearer verify when multi-provider auth (basic, nous, oidc) is enabled  
> **Location**: `O:\workspaces\oss\hermes-agent`  
> **Status**: Crystallized / Ready for Proposal  

---

## 1. Context & Symptom

When Hermes Agent is configured with multiple dashboard authentication providers simultaneously:
- `basic` (password gate)
- `nous` (Nous Portal OAuth)
- `oidc` (`self_hosted` OpenID Connect provider, e.g., Cloudflare Access, Google, Okta, Keycloak)

Inbound requests bearing a JWT minted by the OIDC provider (or any client presenting an OIDC Bearer token or cookie) trigger the following warning in gateway / dashboard logs:

```text
2026-09-04 08:31:06,058 WARNING hermes_cli.dashboard_auth.middleware: dashboard-auth: provider 'nous' unreachable during bearer verify: JWKS lookup failed: Unable to find a signing key that matches: "5d5c91f76a5e7d4bbed61155eead15ed35a2c0374685a92d1f22c4a42b04079a"
```

If the token happens to be expired or invalid across all providers, or during specific edge fallback flows, the middleware treats this transient warning as a hard unreachability condition and returns `503 Service Unavailable` (`{"detail": "Auth provider 'nous' unreachable"}`) rather than `401 Unauthorized` (`{"detail": "invalid_or_expired_session"}`).

---

## 2. Architecture & Call Flow Analysis

```
Inbound HTTP Request (Authorization: Bearer <JWT_from_OIDC>)
                 │
                 ▼
┌────────────────────────────────────────────────────────┐
│ hermes_cli/dashboard_auth/middleware.py                │
│   _verify_bearer(request, access_token)                │
└────────────────────────────────────────────────────────┘
                 │
                 ▼ Iterates list_session_providers()
┌────────────────────────────────────────────────────────┐
│ 1. Provider 'nous' (NousDashboardAuthProvider)         │
│    _verify_jwt(access_token)                           │
│    ┌─────────────────────────────────────────────────┐ │
│    │ PyJWKClient.get_signing_key_from_jwt(token)     │ │
│    │ - Connects to Nous Portal JWKS (Healthy! 200 OK)│ │
│    │ - Reads token header kid: "5d5c91f7..."         │ │
│    │ - Looks up kid in Nous JWKS (Not found!)        │ │
│    │ - Refreshes JWKS, looks up again (Not found!)   │ │
│    │ - Raises PyJWKClientError:                      │ │
│    │   'Unable to find a signing key that matches...'│ │
│    └─────────────────────────────────────────────────┘ │
│    ┌─────────────────────────────────────────────────┐ │
│    │ classify_jwks_lookup_error(exc)                 │ │
│    │ - BUG: Classifies PyJWKClientError as           │ │
│    │   ProviderError("JWKS lookup failed: ...")      │ │
│    └─────────────────────────────────────────────────┘ │
│    Raises ProviderError                                │
└────────────────────────────────────────────────────────┘
                 │
                 ▼ Caught by _verify_bearer / middleware
┌────────────────────────────────────────────────────────┐
│ _log.warning("dashboard-auth: provider 'nous'          │
│               unreachable during bearer verify: ...")  │
│ Sets unreachable_provider = 'nous'                     │
│ (Incorrectly flagged as an IDP network outage!)        │
└────────────────────────────────────────────────────────┘
                 │
                 ▼ Continues provider loop
┌────────────────────────────────────────────────────────┐
│ 2. Provider 'self_hosted' (SelfHostedOIDCProvider)     │
│    _verify_id_token(access_token)                      │
│    - Matches OIDC IdP JWKS -> Validates claims!        │
│    - Returns Session                                   │
└────────────────────────────────────────────────────────┘
```

---

## 3. Root Cause Investigation

### 3.1. Prior Fix Regressed in Commit `2f44998353` (#94558)
Commit `2f44998353` (`fix(dashboard-auth): a non-JWT bearer is "not my token", not "provider unreachable"`) attempted to prevent non-JWT bearers (e.g. opaque peer keys) from triggering 503 errors. It introduced `classify_jwks_lookup_error()` in `hermes_cli/dashboard_auth/base.py`:

```python
def classify_jwks_lookup_error(exc: BaseException) -> Exception:
    try:
        import jwt
    except Exception:
        return ProviderError(f"JWKS lookup failed: {exc!r}")
    if isinstance(exc, jwt.PyJWKClientConnectionError):
        return ProviderError(f"JWKS lookup failed: {exc}")
    if isinstance(exc, (jwt.DecodeError, jwt.PyJWKSetError)):
        return InvalidCodeError(f"token not verifiable by this provider: {exc}")
    if isinstance(exc, jwt.PyJWKClientError):
        return ProviderError(f"JWKS lookup failed: {exc}")
    if isinstance(exc, jwt.InvalidTokenError):
        return InvalidCodeError(f"token not verifiable by this provider: {exc}")
    return ProviderError(f"JWKS lookup failed: {exc!r}")
```

### 3.2. Why `jwt.PyJWKSetError` Was Not Raised
The author assumed that a foreign key ID (`kid`) would raise `jwt.PyJWKSetError`:
> `* jwt.PyJWKSetError — the JWKS was fetched fine but holds no key for this token's kid (rotated/foreign key). The provider was reached; the token is simply not one of ours.`

However, inspecting PyJWT's actual implementation (`jwt/jwks_client.py`):
```python
def get_signing_key(self, kid: str) -> PyJWK:
    signing_keys = self.get_signing_keys()
    signing_key = self.match_kid(signing_keys, kid)

    if not signing_key:
        signing_keys = self.get_signing_keys(refresh=True)
        signing_key = self.match_kid(signing_keys, kid)

        if not signing_key:
            raise PyJWKClientError(
                f'Unable to find a signing key that matches: "{kid}"'
            )

    return signing_key
```

- `PyJWKSetError` is **only** raised by `PyJWKSet.__init__` when the JWKS payload is empty (`{"keys": []}`) or malformed.
- When the JWKS endpoint is healthy and contains valid keys (as Nous Portal does), but **none match the given `kid`**, PyJWT raises **`PyJWKClientError`**.
- Because `PyJWKClientError` is mapped to `ProviderError`, `classify_jwks_lookup_error` turns a simple "key not found for this provider" into a fatal IDP transport outage ("provider unreachable").

### 3.3. Test Gap in `test_opaque_bearer_not_unreachable.py`
The existing test suite used:
```python
@pytest.fixture(scope="module")
def empty_jwks_server():
    # Returns {"keys": []}
```
When keys is empty, `PyJWKSet` raises `PyJWKSetError`. That allowed the unit test to pass while masking the bug that occurs whenever a real JWKS contains keys.

---

## 4. Proposed Remediation

### 4.1. Refine `classify_jwks_lookup_error`
In `hermes_cli/dashboard_auth/base.py`:
When `exc` is an instance of `jwt.PyJWKClientError`:
Check if the error message indicates a key mismatch:
```python
    if isinstance(exc, jwt.PyJWKClientError):
        # A healthy JWKS was retrieved, but no key matched the token's kid.
        # This token was issued by another IDP (e.g. self-hosted OIDC).
        msg = str(exc)
        if "Unable to find a signing key that matches" in msg:
            return InvalidCodeError(f"token not verifiable by this provider: {exc}")
        return ProviderError(f"JWKS lookup failed: {exc}")
```

### 4.2. Verify Sibling Sites
Both `plugins/dashboard_auth/nous/__init__.py` and `plugins/dashboard_auth/self_hosted/__init__.py` route through `classify_jwks_lookup_error`. Fixing it in `base.py` automatically corrects both:
1. Nous provider inspecting OIDC tokens -> returns `None` (moves to next provider, no warning, no 503).
2. OIDC provider inspecting Nous tokens -> returns `None` (moves to next provider, no warning, no 503).

### 4.3. Test Plan
1. Add test case in `tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py` using a mock JWKS server containing valid signing keys (non-empty `keys: [...]`), asserting that a token with an unmatched `kid` returns `None` without raising `ProviderError`.
2. Verify full suite in `tests/plugins/dashboard_auth/` passes.

---

## 5. Next Steps
- Transition to OpenSpec proposal (`/opsx-propose`) to formalize changes to `hermes_cli/dashboard_auth/base.py` and test suites.
