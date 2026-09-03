## ADDED Requirements

### Requirement: Teams adapter enforces allowed_channels

The Teams adapter SHALL enforce `platforms.teams.extra.allowed_channels` by filtering incoming messages. Matching SHALL be performed first against conversation ID (stable), then against conversation name (best-effort alias).

#### Scenario: Message from allowed channel by ID

- **WHEN** `platforms.teams.extra.allowed_channels` contains conversation ID `19:abc123@thread.tacv2`
- **AND** a message arrives from a conversation with ID `19:abc123@thread.tacv2`
- **THEN** the message SHALL be processed normally

#### Scenario: Message from allowed channel by name

- **WHEN** `platforms.teams.extra.allowed_channels` contains `"Inspire-Robotics-Hands"`
- **AND** a message arrives from a conversation with name `"Inspire-Robotics-Hands"` but a different conversation ID not in the list
- **THEN** the message SHALL be processed normally (name-based best-effort match)

#### Scenario: Message from disallowed channel

- **WHEN** `platforms.teams.extra.allowed_channels` is configured
- **AND** a message arrives from a conversation whose ID and name are both NOT in the allowed list
- **THEN** the message SHALL be silently dropped (not forwarded to the gateway)

#### Scenario: No allowed_channels configured

- **WHEN** `platforms.teams.extra.allowed_channels` is NOT configured or is empty
- **THEN** all channels SHALL be allowed (no filtering)

#### Scenario: DM messages bypass channel filtering

- **WHEN** `platforms.teams.extra.allowed_channels` is configured
- **AND** a message arrives as a personal (DM) conversation
- **THEN** the message SHALL NOT be subject to channel filtering (DMs are always allowed through)

### Requirement: Teams adapter enforces free_response_channels

The Teams adapter SHALL support `platforms.teams.extra.free_response_channels` to designate channels where the bot responds to all messages without requiring an @mention. (Note: Receiving unmentioned messages requires `ChannelMessage.Read.Group` RSC permission in Teams).

#### Scenario: Message in free-response channel without mention

- **WHEN** `platforms.teams.extra.free_response_channels` contains `"Inspire-Robotics-Hands"`
- **AND** a message arrives in channel `"Inspire-Robotics-Hands"` without an @mention of the bot
- **THEN** the message SHALL be processed as if the bot was mentioned

#### Scenario: Message in non-free-response channel without mention

- **WHEN** `platforms.teams.extra.free_response_channels` is configured
- **AND** a message arrives in a channel NOT in the free-response list
- **AND** the message does NOT contain an @mention of the bot
- **THEN** the message SHALL be ignored by the adapter

#### Scenario: Free-response matching uses both IDs and names

- **WHEN** `platforms.teams.extra.free_response_channels` contains entries
- **THEN** matching SHALL follow the same ID-first, name-second strategy as `allowed_channels`
