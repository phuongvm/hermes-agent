# desktop-sidebar-navigation Specification Delta

## ADDED Requirements

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
