## 1. Core Route Restoration

- [x] 1.1 Import `render_native_provider_choice_html` into `hermes_cli/dashboard_auth/routes.py` from `login_page.py`
- [x] 1.2 In `auth_native_authorize` (`routes.py`), add the `elif len(native_eligible) > 1:` branch to return `HTMLResponse` rendering `render_native_provider_choice_html` with no-cache headers and preserved PKCE parameters
- [x] 1.3 Ensure fallback handling for single provider and password providers remains intact

## 2. Test Suite Updates & Verification

- [x] 2.1 Reintroduce `NousStubAuthProvider`, `SelfHostedOidcStubAuthProvider`, and `ProviderLinkParser` in `tests/hermes_cli/test_dashboard_auth_native_flow.py`
- [x] 2.2 Re-add `test_native_authorize_renders_multi_provider_chooser` asserting HTTP 200, no-cache headers, provider buttons, and query parameter preservation
- [x] 2.3 Remove or update `test_native_authorize_empty_provider_ambiguous_multiple_oauth_404` to reflect the chooser UI behavior
- [x] 2.4 Run `pytest tests/hermes_cli/test_dashboard_auth_native_flow.py` to verify all native auth test cases pass
- [x] 2.5 Run `pytest tests/hermes_cli/test_dashboard_auth*` to ensure no regression across the broader dashboard auth test suite

## 3. Review & Quality Verification

- [x] 3.1 Verify full coverage of exploration requirements: multi-provider chooser handles Nous vs OIDC gracefully without 404
- [x] 3.2 Verify OpenSpec compliance and ensure all artifacts are consistent
- [x] 3.3 Verify code quality, formatting, and git diff cleanliness
