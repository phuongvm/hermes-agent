# crew-dashboard-ui-enhancements Specification

## Purpose
Defines requirements and verification scenarios for Crew coordination board UI enhancements across Hermes Desktop and Hermes Web Dashboard, covering radar sweep animations for running tasks, dynamic theme synchronization, notification acknowledgment authorization, and dynamic title case board title display.
## Requirements
### Requirement: Active Task Radar Animation
The Crew board MUST animate cards in `running` status with a rotating conic-gradient radar sweep across the card border.

#### Scenario: Running task has live worker
- GIVEN a task has `status == 'running'` and an active worker heartbeat
- WHEN the task card renders on the board
- THEN the card MUST have class `.live` with a 2.2s rotating conic-gradient radar sweep along its border.

#### Scenario: Running task lacks recent heartbeat
- GIVEN a task has `status == 'running'` without recent heartbeat
- WHEN the task card renders on the board
- THEN the card MUST have class `.quiet` with a slower (6s) amber radar crawl.

---

### Requirement: Dynamic Theme Adaptation
The Crew dashboard MUST match the theme (light or dark, background, foreground, border colors) of the host Hermes Desktop or Dashboard.

#### Scenario: Desktop running light theme
- GIVEN Hermes Desktop is running with a light theme (e.g. `--dt-background` is light)
- WHEN the Crew tab is rendered in Hermes Desktop
- THEN the board background, lanes, and cards MUST render with light background tokens matching `--dt-background` and `--ui-text-primary`.

#### Scenario: Runtime theme change
- GIVEN the Crew board is open in an iframe
- WHEN the user toggles light/dark mode in Hermes Desktop
- THEN the Crew board MUST receive the updated theme via `postMessage` and update its CSS variables without requiring a full iframe reload.

---

### Requirement: Notification Clear-All Execution
The 'Clear All' button on the notification panel MUST successfully acknowledge and dismiss all notifications.

#### Scenario: User clicks Clear All
- GIVEN the notification panel displays stuck or completed tasks
- WHEN the user clicks 'Clear All'
- THEN a POST request to `/ack/all` MUST succeed with status 200, dismissing all notification items from the panel.

---

### Requirement: Dynamic Kanban Board Title Display
The Crew dashboard header MUST display the configured display name (`name` in `board.json`) or slug of the currently active Kanban board instead of hardcoding 'crew board'.

#### Scenario: Active board has configured display name
- GIVEN the active Kanban board is `crew` with `name` configured as `Custom Crew` in `board.json`
- WHEN `/board.json` is fetched
- THEN `"board": "crew"` and `"board_name": "Custom Crew"` MUST be present in the JSON response, and the header `<h1>` and `<title>` MUST display "Custom Crew".

#### Scenario: Active board without custom name falls back to formatted slug
- GIVEN the active Kanban board has no custom `name` in `board.json`
- WHEN the dashboard renders
- THEN the header MUST display the formatted board slug (e.g. "Default Board").

