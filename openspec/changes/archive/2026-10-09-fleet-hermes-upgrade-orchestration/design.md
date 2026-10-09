# Design: Fleet Hermes Upgrade Orchestration

## 1. System Architecture

```
                                  [agent4070]
                       (Source & Verification Authority)
                                       │
                      03:00 Cron: Sync Upstream & Verify
                                       │
                         git push origin main (GitHub)
                                       │
                          [scripts/fleet-sync.ps1]
                                       │
           ┌───────────────────────────┴───────────────────────────┐
           ▼                                                       ▼
   [WSL Automated Rollout]                               [Windows On-Demand]
   (Group: wsl-auto)                                     (Target: asus-vb)
           │                                                       │
   ┌───────┴───────┐                                               ▼
   ▼               ▼                                     1. Check user active / PID
[intel_nuc]     [hp_15s]                                 2. Git pull origin/main
   │               │                                     3. Pre-warm dependencies
   ▼               ▼                                     4. Graceful restart via .cmd
1. Auto-stash dirty files                                5. Health probe
2. Git pull --ff-only
3. Pre-warm (No Desktop!)
4. systemctl --user restart
5. Health probe (8642 / 9119)
```

## 2. Declarative Inventory (`tools/fleet/fleet-inventory.json`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "version": "1.0",
  "nodes": [
    {
      "id": "intel_nuc",
      "name": "Intel NUC (WSL Server)",
      "host": "intel_nuc",
      "os": "linux",
      "repo_path": "/home/ubuntu/workspaces/oss/hermes-agent",
      "venv_python": "/home/ubuntu/workspaces/oss/hermes-agent/venv/bin/python",
      "hermes_home": "/home/ubuntu/workspaces/_config/intel-nuc/.hermes",
      "service_manager": "systemd-user",
      "services": ["hermes-gateway.service", "hermes-dashboard.service"],
      "ports": {
        "gateway": 8642,
        "dashboard": 9119
      },
      "build_desktop": false,
      "policy": "auto"
    },
    {
      "id": "hp_15s",
      "name": "HP 15s (WSL Laptop)",
      "host": "hp_15s",
      "os": "linux",
      "repo_path": "/home/phuong/oss/hermes-agent",
      "venv_python": "/home/phuong/oss/hermes-agent/venv/bin/python",
      "hermes_home": "/home/phuong/ir-hermes/.hermes",
      "service_manager": "systemd-user",
      "services": ["hermes-gateway.service", "hermes-dashboard.service"],
      "ports": {
        "gateway": 8642,
        "dashboard": 9119
      },
      "build_desktop": false,
      "policy": "auto"
    },
    {
      "id": "asus-vb",
      "name": "ASUS VB (Windows Workstation)",
      "host": "asus-vb",
      "os": "windows",
      "repo_path": "O:\\workspaces\\oss\\hermes-agent",
      "venv_python": "O:\\workspaces\\oss\\hermes-agent\\.venv\\Scripts\\python.exe",
      "hermes_home": "O:\\workspaces\\_config\\asus-vb\\hermes",
      "service_manager": "windows-cmd",
      "services": ["start-gateway.cmd", "start-dashboard.cmd"],
      "ports": {
        "gateway": 8642,
        "dashboard": 9119
      },
      "build_desktop": false,
      "policy": "on-demand"
    }
  ]
}
```

## 3. Remote Execution Protocols

### 3.1 WSL Headless Execution Protocol (Linux via SSH)
To prevent stalling on interactive prompts or hanging on unbuffered output:
- **SSH Options**: `-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new`
- **Dirty File Auto-Stash**:
  ```bash
  if ! git diff --quiet || ! git diff --cached --quiet; then
    git stash push -m "fleet-sync-autostash-$(date +%s)"
  fi
  ```
- **Fast-Forward Git Pull**:
  ```bash
  git fetch origin main --quiet && git merge --ff-only origin/main
  ```
- **Offline Pre-Warm (Headless)**:
  Bypasses electron-builder and NSIS entirely:
  ```bash
  "$VENV_PYTHON" -I -B -u -m hermes_cli.source_completion --finish-update --prepared
  ```
- **Systemd User Restart**:
  ```bash
  systemctl --user restart hermes-gateway.service hermes-dashboard.service
  ```
- **Health Verification Probe**:
  Loop for up to 30 seconds:
  ```bash
  for i in $(seq 1 15); do
    if curl -fsS http://127.0.0.1:8642/health >/dev/null 2>&1 && \
       curl -fsS http://127.0.0.1:9119/api/status >/dev/null 2>&1; then
      exit 0
    fi
    sleep 2
  done
  exit 1
  ```

### 3.2 Windows On-Demand Execution Protocol (`asus-vb`)
- **Safety Pre-Check**:
  Queries whether `Hermes.exe` is currently running:
  ```powershell
  $p = Get-Process Hermes -ErrorAction SilentlyContinue
  if ($p) {
    Write-Warning "Hermes Desktop is currently running on asus-vb (PID: $($p.Id))."
  }
  ```
- **Command Dispatch**:
  Executes git fast-forward and restart using native PowerShell remoting or SSH command runner.

## 4. Failure Modes & Safety Guarantees

| Failure Scenario | Consequence | Mitigation Strategy |
| :--- | :--- | :--- |
| SSH Connection Failure / Node Offline | Node cannot be reached | Log warning, mark node status as UNREACHABLE in report, continue remaining nodes. |
| Git Merge Conflict on Node | Local modifications collide with origin | Abort merge, pop/restore stash, do NOT restart services, flag node as CONFLICT. |
| Dependency / Build Failure | `source_completion` exits non-zero | Do NOT restart live systemd services. Old running process remains active. |
| Post-Restart Healthcheck Timeout | Service crashed on boot | Query `journalctl --user -u hermes-gateway -n 50`, attach error snippet to report. |
