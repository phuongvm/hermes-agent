## Why

`agent4070` (Windows host) serves as the primary development workstation and authority for Hermes Agent upstream reconciliation, invariant verification (INV-1..INV-9), and release packaging. The daily scheduled cron job (`Hermes - Daily Upstream Sync Watchdog`, `0 3 * * *`) successfully synchronizes `upstream/main` into `origin/main` (`github.com/phuongvm/hermes-agent`), verifies the build, and performs a zero-downtime cutover on `agent4070`.

However, the fleet contains three additional operational nodes that currently suffer from version drift (ranging from 600 to 1,700+ commits behind):
1. `intel_nuc` (WSL Linux, 192.168.100.110): Runs 24/7 background AI services, Gateway, and Dashboard via `systemctl --user`.
2. `hp_15s` (WSL Linux): Mobile node running background Gateway and Dashboard via `systemctl --user`.
3. `asus-vb` (Windows host): Workstation running Gateway and interactive `Hermes.exe` desktop application for human users.

We need a centralized, deterministic, and safe fleet upgrade orchestration workflow that automatically migrates verified updates to headless WSL nodes (`intel_nuc`, `hp_15s`) while providing an isolated on-demand trigger mechanism for the interactive Windows node (`asus-vb`).

## What Changes

1. **Declarative Fleet Inventory (`tools/fleet/fleet-inventory.json`)**:
   - Central definition of all fleet machines: SSH connection alias, platform OS (`linux` vs `windows`), repository path, virtual environment path, service manager (`systemd-user` vs `cmd`), rollout policy (`auto` vs `on-demand`), and port health checks.
2. **Multi-Node Fleet Upgrade Engine (`scripts/fleet-sync.ps1`)**:
   - Single orchestrator script executable from `agent4070`.
   - Supports `-Group wsl-auto` (upgrades `intel_nuc` and `hp_15s`) and `-Target asus-vb` (on-demand manual upgrade).
   - WSL Execution: Skips desktop builds entirely (`hermes-desktop` is not required on headless WSL). Performs auto-stash of dirty modifications, `git merge --ff-only origin/main`, offline venv pre-warming (`hermes_cli.source_completion --finish-update --prepared`), systemd service restart (`systemctl --user restart hermes-gateway hermes-dashboard`), and API health probe.
   - Windows Execution (`asus-vb`): Verifies user activity state, updates code from `origin/main`, pre-warms environment, and gracefully restarts services.
3. **Integration into Daily Watchdog**:
   - `scripts/daily-sync-watchdog.ps1` invokes `fleet-sync.ps1 -Group wsl-auto` immediately following a successful local cutover on `agent4070`.
4. **Aggregated Reporting**:
   - Consolidates fleet health status across all nodes into a unified report delivered to Commander via Buzz.

## Capabilities

### New Capabilities
- `fleet-upgrade-orchestration`: Declarative, multi-node orchestration engine to migrate verified Hermes code and dependency updates across heterogeneous operating systems (Windows and Linux/WSL2) with zero downtime.

### Modified Capabilities
- `hermes-upstream-sync`: Extended to trigger fleet propagation upon verified local cutover.

## Impact

- **Affected Systems**:
  - `agent4070`: Host orchestrator, `daily-sync-watchdog.ps1`, `fleet-sync.ps1`.
  - `intel_nuc`: Repository at `/home/ubuntu/workspaces/oss/hermes-agent`, systemd services `hermes-gateway` and `hermes-dashboard`.
  - `hp_15s`: Repository at `/home/phuong/oss/hermes-agent`, systemd services `hermes-gateway` and `hermes-dashboard`.
  - `asus-vb`: Repository at `O:\workspaces\oss\hermes-agent`, service scripts in `_config/asus-vb`.
- **Dependencies**: OpenSSH client on `agent4070`, systemd user session lingering on WSL nodes, `curl` for HTTP health checks.
