## Context

The Hermes Agent gateway supports 20+ messaging platforms. Most built-in platforms (Telegram, Discord, Slack, etc.) have dedicated YAML-to-env-var bridging logic in `gateway/config.py` that translates `config.yaml` keys like `allow_from` into environment variables (e.g., `TELEGRAM_ALLOWED_USERS`). The gateway auth layer (`_is_user_authorized` in `gateway/run.py`) then reads those env vars to authorize senders.

MS Teams is a **plugin platform** registered at `plugins/platforms/teams/adapter.py`. Plugin platforms declare their auth env var names via `allowed_users_env="TEAMS_ALLOWED_USERS"` at registration. The gateway auth layer dynamically discovers this mapping for plugins (lines 3547-3558 in `run.py`).

**Current state:**
- Config loader successfully ingests `platforms.teams.extra` (including `allowed_users`, `allowed_channels`, `free_response_channels`) into `PlatformConfig.extra`.
- Gateway auth **only** reads `os.getenv("TEAMS_ALLOWED_USERS")` — it never consults `PlatformConfig.extra`.
- Teams adapter builds `user_id` from `activity.from_.aad_object_id` (UUID), so email strings in allowlists never match.
- No channel filtering or free-response logic exists in the Teams adapter.

**Stakeholders:** Gateway operators who want unified `config.yaml` management for Teams alongside other platforms.

## Goals / Non-Goals

**Goals:**
1. Make `platforms.teams.extra.allowed_users` a functional authorization source (env var remains as override).
2. Support both AAD Object IDs and email addresses in `allowed_users`.
3. Implement `allowed_channels` enforcement in the Teams adapter (both conversation IDs and channel names).
4. Implement `free_response_channels` in the Teams adapter, conditioned on verified mention-gating behavior.
5. Add test coverage for all new auth and channel policy paths.

**Non-Goals:**
- Full Microsoft Graph API integration for profile enrichment beyond email/UPN resolution.
- Supporting Teams-specific card/adaptive-card authorization flows.
- Changes to other platform adapters or the generic auth framework.
- Supporting Teams guest/external user identities in v1.

## Decisions

### D1: Read from `PlatformConfig.extra` instead of adding YAML-to-env shims

**Decision**: Extend `_is_user_authorized` to consult `PlatformConfig.extra["allowed_users"]` for plugin platforms when the env var is not set.

**Rationale**: The config is already loaded correctly. Adding more ad-hoc YAML-to-env shims is the wrong abstraction direction. Reading from the already-parsed config object is cleaner and benefits all plugin platforms.

**Alternative considered**: Synthesize `TEAMS_ALLOWED_USERS` env var from `PlatformConfig.extra["allowed_users"]` during config loading. Rejected because it adds yet another env-var shim and doesn't help with structured data (lists vs comma-separated strings).

### D2: Email resolution via SDK payload first, Graph API as fallback

**Decision**: Check the incoming Teams activity payload for existing email/UPN fields (e.g., `activity.from_.email` or tenant-level UPN). If available, populate `source.email` alongside `source.user_id`. If not available in the payload, use Microsoft Graph API (`/users/{aad_id}`) to resolve the email, with caching (TTL ~1 hour).

**Rationale**: The Commander explicitly chose Option C (email-friendly matching) with AAD IDs + email aliases. Payload-first avoids external API calls when the SDK already has the data. Graph fallback ensures email matching works even when the SDK payload is sparse.

**Alternative considered**: AAD-ID-only (documentation-only fix). Rejected per Commander's decision to support email aliases.

### D3: Channel policy uses both IDs and names, IDs preferred

**Decision**: `allowed_channels` entries are matched first against `conversation.id`, then against `conversation.name` as a best-effort alias. IDs are the stable contract; names are convenience.

**Rationale**: The Commander explicitly chose "Both, with IDs preferred." Channel names can change; IDs are stable. Supporting both gives users flexibility while maintaining reliability.

### D4: Free-response channels require Resource-Specific Consent (RSC)

**Decision**: The `free_response_channels` config will be implemented in the adapter to bypass software-level mention checks, but we will explicitly document that Teams bots only receive unmentioned messages if the tenant admin grants `ChannelMessage.Read.Group` (RSC) permissions in the app manifest.

**Rationale**: By default, Teams bots only receive messages where they are @mentioned. We cannot bypass this Teams platform limitation in Python code. The adapter logic will be ready to process unmentioned messages in designated channels, but the capability relies on external Teams configuration.

### D5: Graph API requires User.Read.All application permission

**Decision**: Email resolution via Graph API (`GET /users/{aad_id}`) requires the `User.Read.All` application permission granted via Azure AD admin consent. This will be documented as a prerequisite for using email aliases.

**Rationale**: Since Hermes runs as a background service, it uses Application permissions. Without this permission, the email fallback will fail gracefully to UUID-only matching.

## Risks / Trade-offs

- **[Graph API dependency]** → Mitigated by making Graph resolution optional (payload-first) and caching results. If Graph credentials are unavailable, fall back to UUID-only matching with a warning log.
- **[Channel name instability]** → Mitigated by preferring ID-based matching. Name matching is documented as best-effort, not guaranteed.
- **[SDK payload variability]** → Different Teams SDK versions or tenant configurations may expose different fields. Mitigated by defensive field access with fallback chain.
- **[Performance of Graph lookups]** → Mitigated by in-memory cache with TTL. First message from a new user incurs one API call; subsequent messages use cache.

## Architecture Updates

### Modified Components

1. `gateway/run.py` — `_is_user_authorized()`: Add plugin-platform `config.extra` consultation path.
2. `plugins/platforms/teams/adapter.py` — `_on_message()`: Add email resolution, channel filtering, free-response logic.
3. `plugins/platforms/teams/adapter.py` — `register()`: Expose new config keys in plugin metadata.

### New Components

1. `plugins/platforms/teams/graph_client.py` (optional): Lightweight Microsoft Graph client for email/UPN resolution with caching.


