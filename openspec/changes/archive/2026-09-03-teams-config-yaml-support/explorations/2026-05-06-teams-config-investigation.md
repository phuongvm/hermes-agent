# Exploration: MS Teams Configuration in config.yaml

## Issue
The user reported that configuring `allowed_users`, `allowed_channels`, and `free_response_channels` in `config.yaml` for Microsoft Teams does not work, but setting `TEAMS_ALLOWED_USERS` in `.env` using Azure AD Object IDs does work.

## Findings

### 1. Configuration Ignored in config.yaml
In `gateway/config.py` (around line 677), the system loops over `for plat in Platform:` to bridge settings from `config.yaml` into environment variables (like `TELEGRAM_ALLOWED_USERS`). However, the `teams` platform is a dynamic Plugin Platform and is not explicitly defined in the `Platform` Enum class. Because the loop skips dynamic plugin platforms, the `allowed_users` setting in `config.yaml` under `teams:` is never mapped to `TEAMS_ALLOWED_USERS`. This is why only the `.env` approach currently functions.

### 2. User Identifiers (Emails/Names vs UUIDs)
The `_is_user_authorized` logic in `gateway/run.py` compares the configured allowed users list against the incoming `source.user_id`. In the MS Teams `TeamsAdapter._on_message` implementation, the `source.user_id` is extracted from `activity.from_.aad_object_id` (the Azure AD Object ID, a UUID). The payload provided by the Microsoft Teams SDK does not include the user's email address. Matching by email would require an active integration with the Microsoft Graph API to resolve UUIDs to emails, which is currently not present in the adapter.

### 3. Missing Channel Logic
The `allowed_channels` and `free_response_channels` filtering logic is completely absent from `plugins/platforms/teams/adapter.py`. While adapters like Discord explicitly check environment variables such as `DISCORD_ALLOWED_CHANNELS`, the Teams adapter lacks any equivalent checking logic, causing these settings to be ignored even if they were correctly bridged.

## Conclusion and Next Steps
To resolve this, we need to:
1. Update `gateway/config.py` to properly map `config.yaml` fields for Plugin Platforms into environment variables, or update the authorization layer to read from `PlatformConfig.extra`.
2. Enhance `TeamsAdapter` to support email or User Principal Name (UPN) resolution (e.g., via Microsoft Graph API lookup).
3. Implement channel filtering logic (`allowed_channels`, `free_response_channels`) in `TeamsAdapter`.

**Hard Exit Gate**: The exploration is complete and insights have crystallized. To proceed with the fix, we must formally initiate a Proposal via `/opsx-propose`.
