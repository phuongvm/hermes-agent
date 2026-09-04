# Verification Report: fix-multi-provider-jwks-kid-classification

**Date**: 2026-09-04  
**Change**: `fix-multi-provider-jwks-kid-classification`  
**Schema**: `spec-driven`  
**Auditor**: Agent QA (Process Compliance & Verification Guardian)  
**Target Project**: `hermes-agent` (`O:\workspaces\oss\hermes-agent`)  
**Verdict**: APPROVED — READY FOR ARCHIVE / MERGE  

---

## 1. Executive Summary & Scorecard

Under Zero-Trust I/O rules and the `opsx-verify` workflow (`O:\workspaces\.agents\workflows\opsx-verify.md`), Agent QA conducted an exhaustive 3-dimensional audit (Completeness, Correctness, Coherence) of the implementation for change `fix-multi-provider-jwks-kid-classification`.

| Dimension | Target Criteria | Empirical Finding | Status |
| :--- | :--- | :--- | :---: |
| **Completeness** | All tasks in `tasks.md` completed with verifiable evidence | 2/2 Milestones, 6/6 tasks completed (`[x]`) | ✅ PASS |
| **Correctness** | Delta spec requirement & scenario satisfaction | 1/1 ADDED Requirement, 1/1 Scenario fully implemented | ✅ PASS |
| **Coherence** | Architectural alignment with `design.md` & error taxonomy | Error handling preserved for true network faults; surgical fix | ✅ PASS |
| **Test Verification** | Full test suite execution in `tests/plugins/dashboard_auth/` | 115 passed in 17.24s (0 failures, 0 warnings) | ✅ PASS |
| **OpenSpec Validation** | Schema integrity via `openspec validate` | Change validation passed cleanly | ✅ PASS |
| **Pre-Merge Safety** | Main spec merge safety check without data loss | ADDED requirement only; no overwritten main scenarios | ✅ PASS |

---

## 2. Dimensional Evaluation

### 2.1. Completeness Audit
- Reviewed `openspec/changes/fix-multi-provider-jwks-kid-classification/tasks.md`:
  - **Milestone 1**: Tasks 1.1 and 1.2 marked `[x]` with unit test citations (`test_opaque_bearer_not_unreachable.py`).
  - **Milestone 2**: Tasks 2.1 through 2.4 marked `[x]` with full test execution citations.
- All 6 tasks correspond directly to commits/edits in `hermes_cli/dashboard_auth/base.py` and `tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py`.
- **Finding**: Zero incomplete tasks. Completeness is 100%.

### 2.2. Correctness Audit
- **Delta Specification**: `specs/dashboard-auth/spec.md`
  - **Requirement**: `Foreign Key ID Must Not Trigger Provider Unreachable`
  - **Code Location**: `hermes_cli/dashboard_auth/base.py:145-148`
    ```python
    if isinstance(exc, jwt.PyJWKClientError):
        if "Unable to find a signing key that matches" in str(exc):
            return InvalidCodeError(f"token not verifiable by this provider: {exc}")
        return ProviderError(f"JWKS lookup failed: {exc}")
    ```
- **Scenario Verification**: `Request with Foreign OIDC Key ID evaluated by Nous Provider`
  - When an inbound Bearer token contains an unlisted `kid` against a healthy, populated JWKS:
    1. `classify_jwks_lookup_error` returns `InvalidCodeError` (tested in `test_classifier_maps_unverifiable_token_to_invalid_code[exc2]`).
    2. `NousDashboardAuthProvider.verify_session` returns `None` instead of raising `ProviderError` (tested in `test_foreign_kid_jwt_with_populated_jwks_is_not_unreachable`).
    3. HTTP middleware responds with 401 Unauthorized (not 503) and generates zero "unreachable" warning logs (tested in `test_gated_api_rejects_foreign_kid_bearer_with_401_not_503` and `test_gated_api_rejects_foreign_kid_cookie_with_401_not_503`).
- Genuine transport failures (`PyJWKClientConnectionError`) continue to map strictly to `ProviderError` (tested in `test_real_jwt_with_unreachable_portal_still_raises_provider_error`).

### 2.3. Coherence & Architectural Adherence
- The fix respects the narrow core waist and Sans-I/O exception handling philosophy of Hermes Agent.
- Real provider network failures remain protected by 503 responses, while unknown token keys allow the auth provider pipeline to fall through cleanly to subsequent providers or return 401.
- No regression or unintended side effects observed across sibling providers (`basic`, `drain`, `self_hosted`).

### 2.4. Pre-Merge Safety Analysis
- Delta spec `openspec/changes/fix-multi-provider-jwks-kid-classification/specs/dashboard-auth/spec.md` contains solely `## ADDED REQUIREMENTS`.
- There are no `## MODIFIED Requirements` or `## REMOVED Requirements` blocks.
- Main spec `openspec/specs/dashboard-auth/spec.md` contains 4 existing requirements with 8 scenarios. Merging this change will cleanly append the new requirement without overwriting or deleting any existing scenarios.

---

## 3. Empirical Test Execution Log

Target: `tests/plugins/dashboard_auth/`
Command: `pytest tests/plugins/dashboard_auth/ -v`

```text
============================= test session starts =============================
platform win32 -- Python 3.11.14, pytest-9.1.1, pluggy-1.6.0 -- O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: O:\workspaces\oss\hermes-agent
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 115 items

tests/plugins/dashboard_auth/test_basic_provider.py::TestPasswordHashing::test_hash_then_verify_round_trips PASSED [  0%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestPasswordHashing::test_wrong_password_fails PASSED [  1%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestPasswordHashing::test_malformed_hash_returns_false PASSED [  2%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestPasswordHashing::test_two_hashes_of_same_password_differ PASSED [  3%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_protocol_compliant PASSED [  4%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_supports_password_true PASSED [  5%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_login_mints_session PASSED [  6%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_bad_credentials_raise PASSED [  6%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_verify_round_trips_and_rejects_tamper PASSED [  7%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_access_token_not_accepted_as_refresh PASSED [  8%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_refresh_round_trips PASSED [  9%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_cross_secret_token_does_not_verify PASSED [ 10%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_revoke_is_silent PASSED [ 11%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_oauth_methods_raise_not_implemented PASSED [ 12%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestProvider::test_construction_validates_inputs PASSED [ 13%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestRegister::test_skips_when_no_username PASSED [ 13%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestRegister::test_registers_with_env_plaintext_password PASSED [ 14%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestRegister::test_env_password_overrides_config PASSED [ 15%]
tests/plugins/dashboard_auth/test_basic_provider.py::TestRegister::test_explicit_secret_makes_sessions_portable PASSED [ 16%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestEntropyGate::test_strong_secret_passes PASSED [ 17%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestEntropyGate::test_empty_rejected PASSED [ 18%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestEntropyGate::test_too_short_rejected PASSED [ 19%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestEntropyGate::test_long_but_repeated_rejected PASSED [ 20%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestEntropyGate::test_custom_min_chars_enforced PASSED [ 20%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestProvider::test_protocol_compliance PASSED [ 21%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestProvider::test_supports_token_flag PASSED [ 22%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestProvider::test_is_non_interactive PASSED [ 23%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestProvider::test_verify_token_accepts_matching_secret PASSED [ 24%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestProvider::test_verify_token_rejects_empty PASSED [ 25%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestProvider::test_construction_rejects_weak_secret PASSED [ 26%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestProvider::test_interactive_methods_raise PASSED [ 26%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestRegister::test_skips_when_no_secret PASSED [ 27%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestRegister::test_skips_and_fails_closed_on_weak_secret PASSED [ 28%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestRegister::test_registers_with_strong_env_secret PASSED [ 29%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestRegister::test_config_scope_applied PASSED [ 30%]
tests/plugins/dashboard_auth/test_drain_provider.py::TestRegister::test_config_min_secret_chars_can_reject_otherwise_ok_secret PASSED [ 31%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConstruction::test_protocol_compliance PASSED [ 32%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConstruction::test_name_and_display PASSED [ 33%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConstruction::test_extracts_agent_instance_id PASSED [ 33%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConstruction::test_strips_trailing_slash_from_portal_url PASSED [ 34%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConstruction::test_rejects_malformed_client_id PASSED [ 35%]
tests/plugins/dashboard_auth/test_nous_plugin.py::TestPluginRegister::test_skips_when_client_id_missing PASSED [ 36%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestPluginRegister::test_registers_with_default_portal_url_when_only_client_id_set PASSED [ 37%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestPluginRegister::test_empty_portal_url_env_uses_default PASSED [ 38%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConfigYamlSource::test_config_yaml_only_client_id_registers PASSED [ 39%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConfigYamlSource::test_env_overrides_config_client_id PASSED [ 40%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestConfigYamlSource::test_neither_source_skips_with_helpful_reason PASSED [ 40%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestStartLogin::test_returns_login_start PASSED [ 41%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestStartLogin::test_redirect_url_targets_portal_authorize PASSED [ 42%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestStartLogin::test_authorize_url_has_required_params PASSED [ 43%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestStartLogin::test_code_verifier_in_cookie_payload_43_to_128_chars PASSED [ 44%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestStartLogin::test_state_in_cookie_payload_matches_url_param PASSED [ 45%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestStartLogin::test_two_calls_produce_different_state_and_verifier PASSED [ 46%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestStartLogin::test_allows_http_with_arbitrary_host PASSED [ 46%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestCompleteLogin::test_happy_path_returns_session PASSED [ 47%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestCompleteLogin::test_400_raises_invalid_code PASSED [ 48%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestCompleteLogin::test_500_raises_provider_error PASSED [ 49%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestCompleteLogin::test_missing_access_token_raises PASSED [ 50%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestCompleteLogin::test_unexpected_token_type_raises PASSED [ 51%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestCompleteLogin::test_network_error_raises_provider_error PASSED [ 52%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestCompleteLogin::test_captures_refresh_token_if_present_forward_compat PASSED [ 53%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestVerifySession::test_jwks_client_sends_explicit_http_headers PASSED [ 53%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestVerifySession::test_expired_token_returns_none PASSED [ 54%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestVerifySession::test_wrong_audience_raises_provider_error PASSED [ 55%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestVerifySession::test_verification_failure_message_surfaces_token_claims PASSED [ 56%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestVerifySession::test_agent_instance_id_mismatch_rejected PASSED [ 57%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestVerifySession::test_contract_version_missing_warns_but_succeeds PASSED [ 58%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestVerifySession::test_jwks_unreachable_raises_provider_error PASSED [ 59%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestRefreshAndRevoke::test_refresh_happy_path_returns_rotated_session PASSED [ 60%]
tests/plugins/dashboard_auth/test_nous_provider.py::TestRefreshAndRevoke::test_revoke_is_noop PASSED [ 60%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_classifier_maps_transport_failure_to_provider_error PASSED [ 61%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_classifier_maps_unverifiable_token_to_invalid_code[exc0] PASSED [ 62%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_classifier_maps_unverifiable_token_to_invalid_code[exc1] PASSED [ 63%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_classifier_maps_unverifiable_token_to_invalid_code[exc2] PASSED [ 64%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_classifier_maps_unverifiable_token_to_invalid_code[exc3] PASSED [ 65%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_classifier_keeps_bare_jwk_client_error_as_provider_fault PASSED [ 66%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_opaque_bearer_with_healthy_portal_is_not_unreachable PASSED [ 66%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_foreign_kid_jwt_with_healthy_portal_is_not_unreachable PASSED [ 67%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_foreign_kid_jwt_with_populated_jwks_is_not_unreachable PASSED [ 68%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_real_jwt_with_unreachable_portal_still_raises_provider_error PASSED [ 69%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_opaque_bearer_with_unreachable_portal_is_still_just_not_ours PASSED [ 70%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_self_hosted_provider_shares_the_classification PASSED [ 71%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_gated_api_rejects_opaque_bearer_with_401_not_503 PASSED [ 72%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_gated_api_rejects_opaque_cookie_with_401_not_503 PASSED [ 73%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_gated_api_rejects_foreign_kid_bearer_with_401_not_503 PASSED [ 73%]
tests/plugins/dashboard_auth/test_opaque_bearer_not_unreachable.py::test_gated_api_rejects_foreign_kid_cookie_with_401_not_503 PASSED [ 74%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConstruction::test_protocol_compliance PASSED [ 75%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConstruction::test_strips_trailing_slash_from_issuer PASSED [ 76%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConstruction::test_requires_issuer PASSED [ 77%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConstruction::test_rejects_non_https_issuer PASSED [ 78%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestDiscovery::test_fetches_and_caches PASSED [ 79%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestStartLogin::test_returns_login_start PASSED [ 80%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestStartLogin::test_authorize_url_has_required_params PASSED [ 80%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestStartLogin::test_state_in_cookie_matches_url PASSED [ 81%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestCompleteLogin::test_happy_path_returns_session PASSED [ 82%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestCompleteLogin::test_tolerates_missing_refresh_token PASSED [ 83%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestCompleteLogin::test_missing_id_token_raises PASSED [ 84%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestCompleteLogin::test_400_raises_invalid_code PASSED [ 85%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConfidentialClient::test_public_client_sends_no_secret_or_auth_header PASSED [ 86%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConfidentialClient::test_confidential_defaults_to_basic_when_methods_absent PASSED [ 86%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConfidentialClient::test_basic_url_encodes_reserved_chars_in_secret PASSED [ 87%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConfidentialClient::test_refresh_grant_authenticates_confidential_client PASSED [ 88%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestConfidentialClient::test_secret_not_in_repr_or_log PASSED [ 89%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestVerifySession::test_expired_returns_none PASSED [ 90%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestVerifySession::test_wrong_audience_raises PASSED [ 91%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestVerifySession::test_failure_message_surfaces_claims PASSED [ 92%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestVerifySession::test_jwks_unreachable_raises PASSED [ 93%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestVerifySession::test_jwks_client_sends_explicit_http_headers PASSED [ 93%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestPluginRegister::test_skips_when_unconfigured PASSED [ 94%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestPluginRegister::test_registers_from_env PASSED [ 95%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestPluginRegister::test_env_overrides_config PASSED [ 96%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestPluginRegister::test_config_load_failure_falls_through PASSED [ 97%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestPluginRegister::test_secret_from_env PASSED [ 98%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestPluginRegister::test_env_secret_overrides_config PASSED [ 99%]
tests/plugins/dashboard_auth/test_self_hosted_provider.py::TestPluginRegister::test_empty_env_secret_does_not_shadow_config PASSED [100%]

============================ 115 passed in 17.24s =============================
```

---

## 4. Final Assessment

- **Critical Issues**: 0
- **Warning Issues**: 0
- **Suggestions**: 0
- **Process Compliance**: Fully compliant with OpenSpec and MAS protocols.
- **Recommendation**: **READY FOR ARCHIVE / MERGE**. The Leader may present this report to the Human Principal for explicit archive sign-off.
