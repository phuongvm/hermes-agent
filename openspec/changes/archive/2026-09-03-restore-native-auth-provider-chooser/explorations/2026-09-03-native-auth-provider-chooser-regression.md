# Exploration: Native Auth Multi-Provider Chooser Regression (Nous vs OIDC)

**Date**: 2026-09-03  
**Author**: Claude Agent / phuong_lambert  
**Status**: Completed (Ready for Proposal)  
**Topic**: Root cause analysis and remediation architecture for regression in `/auth/native/authorize` multi-provider selection (Nous vs OIDC).

---

## 1. Problem Statement

When the Hermes gateway is configured with multiple OAuth / OIDC authentication providers (e.g., Nous Research portal + self-hosted Keycloak / Okta OIDC), the Hermes Desktop application (Electron) initiates native sign-in via RFC 8252:
1. Desktop opens the system browser to `/auth/native/authorize` with PKCE parameters (`code_challenge`, `code_challenge_method=S256`, loopback `redirect_uri`, `state`), without hardcoding an initial `provider` query parameter.
2. Rather than presenting a provider selection page ("Sign in with Nous Research" vs "Sign in with Self-Hosted OIDC") while preserving PKCE query parameters, the gateway currently raises:
   ```json
   HTTP 404 Not Found
   {"detail": "Unknown provider: ''"}
   ```
3. The user is completely unable to complete authentication from the Desktop app on multi-provider setups.

---

## 2. Forensic Investigation & Git Archeology

### A. The Original Fix (Commit `caf637502c` / `65b22b0600`)
On 2026-08-02, commit `65b22b0600` (`fix(dashboard): support native auth provider selection`) implemented the multi-provider chooser:
- Added `render_native_provider_choice_html()` in `hermes_cli/dashboard_auth/login_page.py`.
- In `hermes_cli/dashboard_auth/routes.py`, within `auth_native_authorize`:
  - When `provider` was empty and multiple native-eligible providers were found, returned an `HTMLResponse` rendering provider buttons formatted with the original PKCE parameters.
  - Clicking any provider sent a `GET` to `/auth/native/authorize?provider=<selected>&...` which proceeded to the selected IdP.
- Added comprehensive unit tests in `tests/hermes_cli/test_dashboard_auth_native_flow.py`:
  - `test_native_authorize_renders_multi_provider_chooser` verified that two registered providers yielded a 200 HTML chooser preserving `code_challenge`, `redirect_uri`, and `state`.

### B. The Silent Regression (Commit `ed5e17f4b8`)
On 2026-08-07, commit `ed5e17f4b8` (*"fix(auth): /auth/native/authorize 空 provider 自动选择不再统计会被拒绝的密码 provider"*) was merged to fix issue #78906 (ignoring password providers during empty-provider auto-selection).

During that refactor:
1. The author introduced `native_eligible` to exclude `supports_password` providers:
   ```python
   native_eligible = [
       pp
       for pp in list_session_providers()
       if not getattr(pp, "supports_password", False)
   ]
   ```
2. **Accidental Deletion**: The author removed the `elif len(native_eligible) > 1:` block that invoked `render_native_provider_choice_html()`.
3. If `len(native_eligible) > 1`, `p` remained `None`, falling through to:
   ```python
   if p is None:
       raise HTTPException(status_code=404, detail=f"Unknown provider: {provider!r}")
   ```
4. In `tests/hermes_cli/test_dashboard_auth_native_flow.py`, `test_native_authorize_renders_multi_provider_chooser` was deleted, and replaced by `test_native_authorize_empty_provider_ambiguous_multiple_oauth_404`, cementing the regression into CI.
5. In `login_page.py`, `render_native_provider_choice_html()` was left intact as dead code.

### C. Subsequent Evolution (Commit `56f1afc834`)
On 2026-08-14, commit `56f1afc834` extended native sign-in to password providers via system browser autofill (redirecting password providers to `/login` with broker state stored in the PKCE cookie). This further altered native provider dispatch, but left the multi-OAuth 404 behavior untouched.

---

## 3. Architecture & Data Flow

```
                              ┌───────────────────────────────────┐
                              │     Hermes Desktop (Electron)     │
                              └─────────────────┬─────────────────┘
                                                │
                                  1. Opens System Browser:
                                     GET /auth/native/authorize?
                                     code_challenge=...&redirect_uri=127.0.0.1:port
                                                │
                                                ▼
                              ┌───────────────────────────────────┐
                              │   FastAPI Gateway (routes.py)     │
                              └─────────────────┬─────────────────┘
                                                │
                              Eligible Native Session Providers?
                                                │
                   ┌────────────────────────────┼────────────────────────────┐
                   ▼                            ▼                            ▼
          [Single Provider]            [Multiple Providers]          [Zero Providers /
       (e.g., Nous or OIDC only)    (e.g., Nous + Self-Hosted OIDC)   Password Only fallback]
                   │                            │                            │
                   │                            │                            ▼
                   │                            │                   Existing 400/404 logic
                   │                            ▼
                   │                  Render Chooser HTML (200)
                   │             (render_native_provider_choice_html)
                   │                            │
                   │                            ▼
                   │                  User clicks provider button:
                   │                  GET /auth/native/authorize?
                   │                  provider=<name>&code_challenge=...
                   │                            │
                   └───────────────────────────┬┘
                                               │
                                               ▼
                              302 Redirect to Provider IdP (or /login)
                              with broker_state in PKCE cookie
```

---

## 4. Root Cause Summary

| Component | Status on `main` | Required Correction |
|:---|:---|:---|
| `hermes_cli/dashboard_auth/login_page.py` | Function `render_native_provider_choice_html` exists | None. Fully intact. |
| `hermes_cli/dashboard_auth/routes.py` | Missing import & branch for `len(native_eligible) > 1` | Re-import `render_native_provider_choice_html` and return `HTMLResponse` with preserved PKCE query parameters. |
| `tests/hermes_cli/test_dashboard_auth_native_flow.py` | Tests assert 404 on ambiguous providers | Restore multi-provider chooser test asserting 200 and link targets; remove the erroneous 404 assertion for multiple OAuth providers. |

---

## 5. Proposed Remediation Plan

1. **Restore Chooser Routing in `routes.py`**:
   - Re-import `render_native_provider_choice_html` from `login_page.py`.
   - In `auth_native_authorize`:
     ```python
     if len(native_eligible) == 1:
         p = native_eligible[0]
     elif len(native_eligible) > 1:
         return HTMLResponse(
             render_native_provider_choice_html(
                 providers=native_eligible,
                 authorize_path=f"{_prefix(request)}/auth/native/authorize",
                 code_challenge=code_challenge,
                 code_challenge_method=code_challenge_method,
                 redirect_uri=redirect_uri,
                 state=state,
             ),
             headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
         )
     elif not native_eligible:
         sess_providers = list_session_providers()
         if len(sess_providers) == 1:
             p = sess_providers[0]
     ```
2. **Harmonize Test Suite**:
   - Restore `test_native_authorize_renders_multi_provider_chooser` testing Nous + OIDC stub providers.
   - Remove `test_native_authorize_empty_provider_ambiguous_multiple_oauth_404`.
   - Maintain all single-provider and password-provider tests intact.
3. **Verification**:
   - Run `pytest tests/hermes_cli/test_dashboard_auth_native_flow.py` and ensure 100% pass.
   - Run full dashboard auth suite `pytest tests/hermes_cli/test_dashboard_auth*`.

---

## 6. Next Steps

- Transition out of Explore mode.
- Initiate `/opsx-propose restore-native-auth-provider-chooser` to create formal OpenSpec artifacts.
