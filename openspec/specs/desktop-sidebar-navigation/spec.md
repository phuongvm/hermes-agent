# desktop-sidebar-navigation Specification

## Purpose
Defines the layout hierarchy, primary workflow prominence, and collapsible TOOLS disclosure behavior in the Hermes Desktop left sidebar.

## Requirements

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

### Requirement: Crew Link Under TOOLS Section Below OpenSpec
The Hermes Desktop left sidebar SHALL include a dedicated navigation item labeled "Crew" inside the collapsible TOOLS section, ordered immediately below the "OpenSpec" navigation item.

#### Scenario: Crew item placement and icon
- **GIVEN** Hermes Desktop is running with the Crew plugin installed
- **WHEN** the user expands the TOOLS section in the left sidebar
- **THEN** the "Crew" item is visible directly beneath "OpenSpec" with the 'organization' or 'users' codicon.

### Requirement: Center Area Crew Tab Activation
When the user clicks the "Crew" sidebar navigation item, Hermes Desktop SHALL navigate to `/crew` and activate/focus the Crew Dashboard in the center workspace pane.

#### Scenario: First activation
- **GIVEN** the user is viewing a chat session
- **WHEN** the user clicks "Crew" in the sidebar
- **THEN** the workspace reveals the Crew Dashboard full-page view displaying the live board.

#### Scenario: Re-activation of existing tab/tile
- **GIVEN** the Crew Dashboard is already open or docked as a route tile
- **WHEN** the user clicks "Crew" in the sidebar again
- **THEN** the existing Crew view is focused and brought to front without reloading or duplicating the pane.

#### Scenario: Split pane view
- **GIVEN** the user right-clicks the "Crew" sidebar item
- **WHEN** the user selects "Open in split"
- **THEN** a route tile for `/crew` is opened docked beside the active chat.
