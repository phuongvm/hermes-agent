## Why

When the Hermes gateway is configured with multiple interactive authentication providers (e.g., Nous Research Portal together with a self-hosted Keycloak/Okta OIDC provider), the Desktop application (Electron) cannot sign in. Initiating the RFC 8252 native OAuth PKCE flow (`/auth/native/authorize`) with an empty provider parameter returns `HTTP 404: Unknown provider: ''` instead of rendering a provider selection page.

This behavior is a silent regression introduced in commit `ed5e17f4b8` while resolving issue #78906 (filtering password providers out of empty-provider auto-selection). The original multi-provider chooser implemented in commit `65b22b0600` was accidentally deleted from `hermes_cli/dashboard_auth/routes.py`, leaving the existing UI helper `render_native_provider_choice_html` as dead code. Restoring this capability is required so desktop users can choose between Nous and OIDC sign-in without 404 failures.

## What Changes

- Restore multi-provider chooser dispatch in `hermes_cli/dashboard_auth/routes.py` for `/auth/native/authorize`:
  - When `provider` is omitted and multiple native-eligible providers exist (`len(native_eligible) > 1`), return an HTML response rendering the chooser page via `render_native_provider_choice_html`.
  - Ensure all PKCE parameters (`code_challenge`, `code_challenge_method`, `redirect_uri`, `state`) are preserved in each provider's authorize link.
- Update and restore native OAuth flow tests in `tests/hermes_cli/test_dashboard_auth_native_flow.py`:
  - Reintroduce `test_native_authorize_renders_multi_provider_chooser` to verify multi-provider selection HTML (HTTP 200, no-cache headers, provider buttons, preserved PKCE parameters).
  - Replace `test_native_authorize_empty_provider_ambiguous_multiple_oauth_404` (which cemented the regression) with a test asserting the chooser HTML response.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `dashboard-auth`: Add requirement for native authorization provider disambiguation so that multi-OAuth/OIDC deployments present a user-facing chooser preserving PKCE parameters rather than failing with HTTP 404.

## Impact

- Affected files:
  - `hermes_cli/dashboard_auth/routes.py`
  - `tests/hermes_cli/test_dashboard_auth_native_flow.py`
- APIs: `/auth/native/authorize` returns `HTTP 200 (HTML)` instead of `HTTP 404 (JSON)` when `provider` is omitted on gateways configured with multiple OAuth/OIDC providers.
- Dependencies: None. No new external libraries or schema migrations required.
