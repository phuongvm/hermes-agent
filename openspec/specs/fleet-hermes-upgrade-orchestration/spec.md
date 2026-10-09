# fleet-hermes-upgrade-orchestration Specification

## Purpose
TBD - created by archiving change fleet-hermes-upgrade-orchestration. Update Purpose after archive.
## Requirements
### Requirement: Declarative Fleet Inventory Specification
The fleet upgrade system SHALL maintain a structured JSON inventory (`tools/fleet/fleet-inventory.json`) defining all managed cluster nodes, their target OS, paths, service managers, rollout policies, and health endpoints.

#### Scenario: Valid fleet inventory resolution
- **GIVEN** a valid `fleet-inventory.json` file containing node entries for `intel_nuc`, `hp_15s`, and `asus-vb`
- **WHEN** the fleet orchestrator loads the configuration
- **THEN** each node is successfully resolved with its specific execution adapter (`systemd-user` or `windows-cmd`) and rollout policy (`auto` or `on-demand`).

### Requirement: WSL Automated Node Rollout Specification
The system SHALL support unattended, zero-downtime upgrades of Linux/WSL nodes without requiring graphical dependencies or building desktop installers.

#### Scenario: Headless WSL fast-forward upgrade
- **GIVEN** a WSL node (`intel_nuc` or `hp_15s`) running Hermes Gateway and Dashboard via `systemctl --user`
- **WHEN** the fleet orchestrator executes an automated upgrade for group `wsl-auto`
- **THEN** local dirty changes are safely stashed, code is fast-forwarded to `origin/main`, dependencies are pre-warmed offline without building Electron desktop, services are restarted via `systemctl --user`, and both `/health` (8642) and `/api/status` (9119) return HTTP 200 OK within 30 seconds.

### Requirement: Windows On-Demand Node Rollout Specification
The system SHALL enforce an on-demand-only execution policy for interactive Windows workstations (`asus-vb`) to prevent disrupting active human sessions.

#### Scenario: On-demand execution with user awareness
- **GIVEN** `asus-vb` is designated with rollout policy `on-demand`
- **WHEN** a scheduled automated fleet run occurs
- **THEN** `asus-vb` is explicitly excluded from the automated rollout.
- **WHEN** the operator explicitly triggers an upgrade with `-Target asus-vb`
- **THEN** the orchestrator verifies whether `Hermes.exe` is actively running, upgrades the repository, and completes the service transition.

### Requirement: Fail-Closed Node Health and Error Reporting
The system SHALL fail closed if any individual node encounters a git conflict, build failure, or health check timeout, preserving existing running services and reporting diagnostics.

#### Scenario: Git conflict or healthcheck timeout
- **GIVEN** a target node has conflicting unmergeable files or fails to respond to HTTP health probes after restart
- **WHEN** the upgrade fails
- **THEN** live services are not restarted with broken code, error diagnostics are recorded, and the failure is summarized in the final execution report without aborting updates to unaffected nodes.

