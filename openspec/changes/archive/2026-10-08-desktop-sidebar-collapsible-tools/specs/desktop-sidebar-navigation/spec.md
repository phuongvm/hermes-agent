# Desktop Sidebar Navigation Specification

## ADDED Requirements

### Requirement: Desktop Sidebar Primary Navigation Promotion
The Hermes Desktop left sidebar SHALL prioritize primary daily workflow items (Sessions, Projects, Pinned, Search) above utility configuration links, keeping New Session directly accessible at the top.

#### Scenario: Primary navigation prominence
- **GIVEN** the Hermes Desktop left sidebar is mounted
- **WHEN** the user views the navigation list
- **THEN** Sessions, Projects, Pinned, and Search are positioned in the top section directly below or alongside the New Session action.

### Requirement: Collapsible TOOLS Group
The Hermes Desktop left sidebar SHALL group secondary and configuration links (Capabilities, Messaging, Cron, Settings, Webhooks) into a dedicated collapsible container labeled "TOOLS", which defaults to collapsed (toggled off).

#### Scenario: Default collapsed state
- **GIVEN** Hermes Desktop is launched
- **WHEN** the left sidebar renders
- **THEN** the TOOLS group is rendered in a collapsed state, hiding the contained utility links until expanded.

#### Scenario: User toggles TOOLS group
- **GIVEN** the TOOLS group is collapsed
- **WHEN** the user clicks the TOOLS toggle button
- **THEN** the section expands smoothly to reveal Capabilities, Messaging, Cron, Settings, and other utility links without disrupting active session navigation.
