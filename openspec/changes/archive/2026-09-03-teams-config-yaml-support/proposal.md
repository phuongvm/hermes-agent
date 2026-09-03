## Why

The MS Teams connector accepts `platforms.teams.extra.allowed_users`, `allowed_channels`, and `free_response_channels` in `config.yaml`, and the config loader successfully ingests these values into `PlatformConfig.extra` at runtime. However, the gateway authorization layer (`_is_user_authorized`) and the Teams adapter never read from `PlatformConfig.extra` — they only check the `TEAMS_ALLOWED_USERS` environment variable. This means users must duplicate their configuration in `.env` files using raw Azure AD Object IDs, defeating the purpose of the unified `config.yaml` approach used by other platforms.

Additionally, Teams sender identity is based on `aad_object_id` (a UUID), making email-based allowlists non-functional. Users expect to configure human-readable identifiers (emails) as they do for other platforms.

Finally, `allowed_channels` and `free_response_channels` have no enforcement logic in the Teams adapter, making those config keys completely inert.

## What Changes

- **Teams auth consumption**: Gateway auth will read `PlatformConfig.extra["allowed_users"]` for Teams, falling back to `TEAMS_ALLOWED_USERS` env var as an explicit override.
- **Email-friendly user matching**: The Teams adapter will support both AAD Object IDs and email aliases in `allowed_users`. The adapter will check the incoming SDK payload for UPN/email fields; if unavailable, it will resolve via Microsoft Graph API as a secondary path.
- **Channel allowlist enforcement**: The Teams adapter will enforce `allowed_channels` by matching against conversation IDs (preferred) and channel names (best-effort alias).
- **Free-response channel support**: The Teams adapter will support `free_response_channels`, gated on verified mention-gating behavior in the Teams SDK.
- **Documentation**: Update Teams docs to clarify supported identity contracts.

## Capabilities

### New Capabilities
- `teams-config-auth`: Support for reading Teams `allowed_users` from `PlatformConfig.extra` with email alias resolution
- `teams-channel-policy`: Enforcement of `allowed_channels` and `free_response_channels` in the Teams adapter

### Modified Capabilities
<!-- No existing specs to modify -->

## Impact

- **Code**: `gateway/run.py` (`_is_user_authorized`), `plugins/platforms/teams/adapter.py` (message handling, plugin registration)
- **Config**: `platforms.teams.extra` keys gain runtime enforcement (currently inert)
- **Dependencies**: Potential new dependency on Microsoft Graph SDK for email resolution (if SDK payload lacks UPN)
- **Documentation**: `website/docs/user-guide/messaging/teams.md` must be updated with supported identity contracts
- **Testing**: New test coverage needed for config-driven auth, email matching, and channel filtering
