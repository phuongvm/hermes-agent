## 1. Teams Auth — Config-Driven Allowlist Consumption

- [x] 1.1 Extend `_is_user_authorized` in `gateway/run.py` to consult `PlatformConfig.extra["allowed_users"]` for plugin platforms when the platform-specific env var (e.g., `TEAMS_ALLOWED_USERS`) is not set
- [x] 1.2 Ensure env var `TEAMS_ALLOWED_USERS` takes precedence over `PlatformConfig.extra["allowed_users"]` when both are set
- [x] 1.3 Verify that an empty/unset config and empty/unset env var falls through to `GATEWAY_ALLOWED_USERS` / `GATEWAY_ALLOW_ALL_USERS` as expected

## 2. Teams Auth — Email Alias Resolution

- [x] 2.1 Investigate the Teams Bot Framework SDK payload (`activity.from_`) for existing email/UPN fields beyond `aad_object_id` and `name`
- [x] 2.2 If email/UPN is available in the SDK payload, populate it into the `SessionSource` (e.g., as an additional check_id in auth) in `plugins/platforms/teams/adapter.py`
- [x] 2.3 Create `plugins/platforms/teams/graph_client.py` — lightweight Microsoft Graph client for `/users/{aad_id}` email/UPN resolution with in-memory TTL cache (default 1 hour)
- [x] 2.4 Integrate Graph client fallback into the Teams adapter: when `allowed_users` contains email entries and the SDK payload lacks email, call Graph API to resolve
- [x] 2.5 Add graceful degradation: if Graph credentials are not configured or API fails, log a warning and fall back to UUID-only matching
- [x] 2.6 Document required Graph API permissions (`User.Read.All` or equivalent) in `website/docs/user-guide/messaging/teams.md` (to be done in group 6)

## 3. Teams Channel Policy — Allowed Channels

- [x] 3.1 In `plugins/platforms/teams/adapter.py`, read `PlatformConfig.extra["allowed_channels"]` during adapter initialization
- [x] 3.2 Implement channel filtering in `_on_message`: match incoming `conversation.id` first (stable), then `conversation.name` (best-effort alias)
- [x] 3.3 Ensure DM (personal) conversations bypass channel filtering
- [x] 3.4 Silently drop messages from channels not in the allowed list (no error response)

## 4. Teams Channel Policy — Free Response Channels

- [x] 4.1 Implement `free_response_channels` support: bypass adapter-level mention checks for designated channels
- [x] 4.2 Match `free_response_channels` entries using the same ID-first, name-second strategy as `allowed_channels`

## 5. Testing

- [x] 5.1 Add unit test: config-driven Teams allowlist (AAD Object ID in `PlatformConfig.extra["allowed_users"]`) authorizes correctly
- [x] 5.2 Add unit test: env var `TEAMS_ALLOWED_USERS` overrides `PlatformConfig.extra["allowed_users"]`
- [x] 5.3 Add unit test: email in allowlist matches when email is available in SDK payload or via Graph resolution
- [x] 5.4 Add unit test: email in allowlist fails gracefully when Graph is unavailable (UUID-only fallback)
- [x] 5.5 Add unit test: `allowed_channels` filters by conversation ID
- [x] 5.6 Add unit test: `allowed_channels` filters by channel name (best-effort)
- [x] 5.7 Add unit test: DM messages bypass `allowed_channels` filtering
- [x] 5.8 Add unit test: `free_response_channels` bypasses mention-gating for designated channels
- [x] 5.9 Run the full test suite to ensure no regressions in Hermes core or Teams adapter

## 6. Documentation

- [x] 6.1 Update `website/docs/user-guide/messaging/teams.md` to document the new `allowed_channels` and `free_response_channels` lists in the `config.yaml` example
- [x] 6.2 Document the Graph API fallback feature: explain that while `allowed_users` accepts emails, a tenant admin must grant `User.Read.All` Application Permission to the Azure AD app for it to work
- [x] 6.3 Document the RSC requirement: explain that for `free_response_channels` to bypass mention-gating, the bot must be granted `ChannelMessage.Read.Group` RSC permissions so it actually receives unmentioned messages.
