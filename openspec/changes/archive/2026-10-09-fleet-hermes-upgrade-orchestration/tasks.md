## 1. Declarative Fleet Inventory & Configuration
- [x] 1.1 Create `references/fleet-inventory.json` defining `intel_nuc`, `hp_15s`, and `asus-vb`

## 2. Multi-Node Fleet Upgrade Engine
- [x] 2.1 Implement `O:\workspaces\.agents\skills\hermes-upstream-sync\scripts\fleet-sync.ps1` with `-Group wsl-auto` and `-Target <node>` parameters
- [x] 2.2 Implement auto-stash, headless pre-warming (skipping desktop build), and systemd restart logic for WSL nodes
- [x] 2.3 Implement interactive user safety check and graceful restart for Windows node `asus-vb`

## 3. Integration & Live Verification
- [x] 3.1 Integrate `fleet-sync.ps1 -Group wsl-auto` into `daily-sync-watchdog.ps1` after local cutover
- [x] 3.2 Execute live rollout on `intel_nuc` and verify `/health` and `/api/status`
- [x] 3.3 Execute live rollout on `hp_15s` and verify `/health` and `/api/status`
- [x] 3.4 Test on-demand dry-run / safety inspection for `asus-vb`
- [x] 3.5 Validate OpenSpec artifacts and deliver aggregated execution report
