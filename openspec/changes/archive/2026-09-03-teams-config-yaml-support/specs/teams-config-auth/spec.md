## ADDED Requirements

### Requirement: Teams authorization reads from PlatformConfig.extra

The gateway authorization layer SHALL read `platforms.teams.extra.allowed_users` from the loaded `PlatformConfig` when `TEAMS_ALLOWED_USERS` environment variable is not set. When the environment variable IS set, it SHALL take precedence over the config file value.

#### Scenario: Config-driven authorization with AAD Object ID

- **WHEN** `platforms.teams.extra.allowed_users` contains AAD Object ID `fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f` in `config.yaml`
- **AND** `TEAMS_ALLOWED_USERS` environment variable is not set
- **THEN** a Teams message from a user with AAD Object ID `fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f` SHALL be authorized

#### Scenario: Environment variable overrides config.yaml

- **WHEN** `platforms.teams.extra.allowed_users` contains `["user-a-uuid"]` in `config.yaml`
- **AND** `TEAMS_ALLOWED_USERS` environment variable is set to `user-b-uuid`
- **THEN** only `user-b-uuid` SHALL be authorized (config.yaml value is ignored)

#### Scenario: Empty config and empty env var

- **WHEN** neither `platforms.teams.extra.allowed_users` nor `TEAMS_ALLOWED_USERS` is configured
- **THEN** Teams authorization SHALL fall through to the global allowlist (`GATEWAY_ALLOWED_USERS`) or global allow-all check

### Requirement: Teams authorization supports email aliases alongside AAD Object IDs

The system SHALL accept both AAD Object IDs (UUIDs) and email addresses in `allowed_users` lists for Teams. When an email address is present in the allowlist, the system SHALL attempt to match it against the sender's identity by:

1. Checking the incoming Teams activity payload for email/UPN fields
2. If not available in the payload, resolving the sender's AAD Object ID to an email via Microsoft Graph API

#### Scenario: Email-based authorization via payload

- **WHEN** `allowed_users` contains `phuong@optimalitypro.com`
- **AND** the incoming Teams activity payload includes the sender's email as `phuong@optimalitypro.com`
- **THEN** the message SHALL be authorized

#### Scenario: Email-based authorization via Graph API fallback

- **WHEN** `allowed_users` contains `phuong@optimalitypro.com`
- **AND** the incoming Teams activity payload does NOT include the sender's email
- **AND** the Microsoft Graph API resolves the sender's AAD Object ID to `phuong@optimalitypro.com`
- **THEN** the message SHALL be authorized

#### Scenario: Email resolution failure graceful degradation

- **WHEN** `allowed_users` contains only email addresses
- **AND** the Teams activity payload does not include email
- **AND** Microsoft Graph API credentials are not configured or the API call fails
- **THEN** the system SHALL log a warning indicating email resolution is unavailable
- **AND** the authorization check SHALL fall through to UUID-based matching only

#### Scenario: Mixed AAD IDs and emails in allowlist

- **WHEN** `allowed_users` contains `["fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f", "hao.nguyen@optimalitypro.com"]`
- **THEN** a message from AAD ID `fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f` SHALL be authorized via direct ID match
- **AND** a message from a user whose email resolves to `hao.nguyen@optimalitypro.com` SHALL be authorized via email match

### Requirement: Graph API email resolution uses caching

The system SHALL cache email resolution results from the Microsoft Graph API to avoid repeated API calls for the same user.

#### Scenario: Cached resolution on subsequent messages

- **WHEN** a user's AAD Object ID has been resolved to an email via Graph API
- **THEN** subsequent messages from the same user SHALL use the cached email without making another Graph API call
- **AND** the cache entry SHALL expire after a configurable TTL (default: 1 hour)
