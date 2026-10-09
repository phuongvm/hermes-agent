# desktop-sidebar-navigation Specification Delta

## ADDED Requirements

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
