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

### Requirement: Declarative Route Tile Tab Opening from Sidebar
The Hermes Desktop navigation system SHALL allow contributed sidebar items to declare `asTile: true`, causing clicks to open or focus the target route as a dedicated tab in the center area tabstrip rather than triggering full-page navigation.

#### Scenario: Opening Crew sidebar item as a center tab
- **GIVEN** Hermes Desktop is running with the Crew plugin declaring `asTile: true`
- **WHEN** the user clicks "Crew" in the TOOLS sidebar section
- **THEN** a new tab titled "Crew" (`route-tile:/crew`) appears in the active center area tabstrip alongside the active chat tab.

#### Scenario: Re-focusing an already open tab
- **GIVEN** the "Crew" tab (`route-tile:/crew`) is already open in the center area tabstrip
- **WHEN** the user clicks "Crew" in the sidebar again
- **THEN** the existing "Crew" tab is activated and focused without duplicating the tab or reloading the iframe.

#### Scenario: Preserving tabstrip visibility on tile route
- **GIVEN** the user has opened the "Crew" tab in the center area
- **WHEN** the user views the Crew Dashboard
- **THEN** the center area tabstrip remains visible, allowing the user to click between the Chat tab and the Crew tab with a single click.
