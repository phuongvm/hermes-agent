# Tasks: Fix Multi-Provider Dashboard Auth Foreign KID JWKS Lookup Failure

## Milestones

- [x] 1. Core Error Classification Fix <!-- id: 1 -->
  - [x] 1.1 Update `classify_jwks_lookup_error` in `hermes_cli/dashboard_auth/base.py` to classify "Unable to find a signing key that matches" as `InvalidCodeError` <!-- id: 1.1 -->
  - [x] 1.2 Verify `classify_jwks_lookup_error` behavior with unit tests <!-- id: 1.2 -->

- [x] 2. Test Suite & Multi-Provider Regression Tests <!-- id: 2 -->
  - [x] 2.1 Add test fixture with populated JWKS (non-empty keys) in `tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py` <!-- id: 2.1 -->
  - [x] 2.2 Add unit test for foreign `kid` against populated JWKS returning `None` without raising `ProviderError` <!-- id: 2.2 -->
  - [x] 2.3 Add HTTP integration test asserting 401 (not 503) and verifying no "unreachable" warning in logs <!-- id: 2.3 -->
  - [x] 2.4 Run complete test suite in `tests/plugins/dashboard_auth/` and ensure all tests pass <!-- id: 2.4 -->

## Verification Evidence
- Milestone 1:
  - `pytest tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py`: 13/13 passed.
- Milestone 2:
  - `pytest tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py`: 16/16 passed.
  - `pytest tests/plugins/dashboard_auth/`: 115/115 passed.
  - Verified populated JWKS (`populated_jwks_server`) returns `None` for foreign `kid` without raising `ProviderError`.
  - Verified HTTP integration test asserts 401 Unauthorized (not 503) on bearer token with foreign `kid` and zero `unreachable` warnings in `caplog`.
