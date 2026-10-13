# Exploration Report: intel-nuc Weekly Sentinel

**Date**: 2026-10-13
**Author**: coder (Kanban card t_bb397dbc)
**Target area**: `skills/devops/intel-nuc-sentinel/` (new bundled skill), `tests/skills/test_intel_nuc_sentinel_skill.py`
**Host**: intel_nuc / DESKTOP-01, 192.168.100.110 (Windows 11 + WSL2 Ubuntu 24.04.1 + Docker Desktop 29.8.2)

## 1. Problem statement

The owner wants a weekly check of intel-nuc that covers more than storage: container liveness,
storage and VHDX slack, datastore telemetry, host resources and security posture. The check has
to run the same way each week, collect every metric with a tool rather than an agent's estimate,
show the evidence behind each number, and never change the host.

Recent incidents on this host show why each area matters:

- t_53f11012 (storage audit): 4.48 GB of container json logs with no `max-size`, ~74.7 GB of slack
  in `docker_data.vhdx`, zookeeper txn logs with no autopurge, and orphaned dirs under
  `/home/ubuntu/volumes`. Report: `O:/workspaces/docs/intel_nuc_docker_storage_report.md`.
- t_c57d5ba5 (build-cache prune, 38 GB) and the Phase 1 cleanup (log truncation, quarantine of
  orphans to `D:\quarantine`) freed space inside the VHDX, but did not return it to Windows.
- Nobody re-measured after those changes, and no remediation has a scheduled re-check.

## 2. Ground truth gathered for this exploration (2026-10-13, read-only)

Every value below was read live over `ssh 192.168.100.110` during this card. Commands are
reproduced in §6.

| Fact | Value | Source command |
|---|---|---|
| Login distro | Ubuntu 24.04.1, kernel 5.15.153.1-microsoft-standard-WSL2, user `phuong` | `uname -a`, `/etc/os-release` |
| Passwordless sudo | **no** (`sudo: a password is required`) | `sudo -n true` |
| Root without password | yes, via `wsl.exe -d Ubuntu -u root` (uid 0) | `wsl.exe ... -e sh -s` + `id -u` |
| Docker Desktop VM | `wsl.exe -d docker-desktop` runs as uid 0, data disk at `/mnt/docker-desktop-disk` | `id -u`, `df` |
| Containers | 31 running / 31 total | `docker ps -a`, `docker info` |
| Unhealthy | `loki` (`Up 7 hours (unhealthy)`) | `docker ps` |
| Log driver | all 31 `json-file`, `LogConfig.Config = {}` (no max-size) | `docker inspect` |
| C: | 498.77 GB, 61.46 GB free (88 %) | `df -B1 /mnt/c` = `Win32_LogicalDisk` |
| `docker_data.vhdx` | 110,840,774,656 bytes; 31.38 GB used inside | `ls -l` / `Get-Item`, `df` in docker-desktop |
| Ubuntu `ext4.vhdx` | 194,295,889,920 bytes; 151.9 GB used inside | `ls -l`, `df /` as root |
| Postgres | `honcho` 4.24 GB of 5.18 GB total; 65/100 connections; XID age 6.4 M | `psql` read-only |
| Postgres read-only proof | `CREATE TABLE` refused: `cannot execute CREATE TABLE in a read-only transaction` | `PGOPTIONS=-c default_transaction_read_only=on` |
| ClickHouse | 26.6.1.1193; parts: langfuse 3.74 GB, otel 0.70 GB, system 0.40 GB; 23 `system.*_log` tables without TTL | `clickhouse-client --readonly=1` |
| Zookeeper | `ZOO_AUTOPURGE_PURGEINTERVAL=0`, `SNAPRETAINCOUNT=3`; datalog now 340 KB | `docker exec ... env`, `du` |
| Redis | both PONG, AOF on, `rdb_last_bgsave_status:ok`, `maxmemory:0` | `redis-cli INFO` |
| sshd (WSL Ubuntu, effective) | `passwordauthentication yes`, `permitrootlogin without-password`, `pubkeyauthentication yes` | `sshd -T` as root |
| apt | 100 upgradable, 4 from `-security`; unattended-upgrades on; no reboot-required flag | `apt list --upgradable`, `20auto-upgrades` |
| Windows | Defender AV + RTP on, signature age 0 d; 3 firewall profiles on; last hotfix KB5126052 on 2026-09-10; no reboot pending; 68.5 GB RAM, ~46 % free | PowerShell CIM / `Get-MpComputerStatus` / `Get-HotFix` |
| Published datastore ports | postgres 5432, clickhouse 8123/9000, kafka 9092, neo4j 7474/7687, redis 6379, ... on all interfaces | `docker inspect` PortBindings |

Two surprises shaped the design:

1. `docker inspect --format '{{.State.Health.Status}}'` errors (`map has no entry for key "Health"`)
   on containers without a healthcheck, and that failure took out the whole inspect section on the
   first live run. The template now uses `{{with index .State "Health"}}`.
2. On Windows, Python text-mode stdin turns `\n` into `\r\n`; the remote `sh` then parsed `2>&1\r`
   as a bad fd and every section failed. Probe scripts are now sent as LF bytes, and a test pins it.

## 3. Options considered

| Option | Verdict | Why |
|---|---|---|
| A. Agent runs ad-hoc shell each week from a prompt | Rejected | Commands drift week to week, numbers are not reproducible, and a model can type a mutating command by accident. That is the "guessing" the owner wants gone. |
| B. Agent on the NUC (node exporter, Prometheus alerts) | Deferred | Prometheus/Grafana already run there, but they cover neither VHDX slack, Windows Defender/hotfix state, sshd settings nor Postgres XID age without new exporters on a production host. Out of scope for a read-only card. |
| C. **Fixed probe catalog + deterministic evaluator shipped as a skill, run weekly by cron** | **Chosen** | One script owns every remote command. Its output is stored raw, evaluated by pure functions, hashed and re-verifiable. The agent only reads and explains the report. Fits the Hermes footprint ladder (CLI script + skill, no core change). |

## 4. Architectural seams and invariants

- **Skill placement**: `skills/devops/intel-nuc-sentinel/` with `scripts/`, `references/`,
  `templates/`. Stdlib only, so `platforms: [linux, macos, windows]` holds (no POSIX-only imports;
  the remote side is POSIX, the agent side is not).
- **Cron**: `cron/scheduler_script.py::_resolve_script_path` only runs scripts inside
  `<HERMES_HOME>/scripts/`, so the skill ships `templates/cron_wrapper.py` that the owner copies
  there; it `runpy`s the installed skill's `sentinel.py run`. Pre-run script stdout is injected into
  the cron agent prompt; failures surface through the scheduler's existing error path.
- **Read-only**: no `sudo` (unavailable non-interactively anyway); root inside WSL only to read
  container-owned data; datastore sessions forced read-only server-side; a denylist guard on every
  probe and remote command before `ssh` runs.
- **Zero guessing**: a failed section produces `UNKNOWN`, never a default; disagreeing sources
  produce `MISMATCH`, never a pick.
- **Profile scope**: output defaults to `$HERMES_HOME/sentinel/intel-nuc`; nothing under `~/.hermes`.

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A future edit adds a mutating probe | `assert_read_only` refuses the catalog at load (exit 2); parametrised tests cover rm/docker restart/prune/compose/apt/systemctl/wsl --shutdown/SQL DDL+DML/Redis writes/PowerShell mutators/redirects. |
| Config injection into the remote shell | Strict token regex for every interpolated value; Windows paths single-quoted for PowerShell; ssh `ProxyCommand`/`LocalCommand` refused. |
| Secrets leaking into evidence | `docker inspect` uses a field-selecting template (never `Config.Env`); Postgres password never read; the cron runner additionally redacts script output. |
| Load on production | 7 ssh sessions, ~12 s total in the live run; `du` limited to the volume root and three data dirs. |
| Host-specific config copied to another host | SKILL.md Pitfalls: build a new `--config` from facts read off that host. |
| Weekly deltas reset by a failed run | Baseline keeps the previous value for any section a run could not read (tested). |

## 6. Reproduce

```bash
python skills/devops/intel-nuc-sentinel/scripts/sentinel.py plan
python skills/devops/intel-nuc-sentinel/scripts/sentinel.py run --out-dir <dir>
python skills/devops/intel-nuc-sentinel/scripts/sentinel.py verify --run <dir>/runs/<stamp>
```

Live evidence for this card:
`O:/workspaces/_config/agent4070/hermes/kanban/boards/crew/workspaces/t_bb397dbc/sentinel/runs/`
(`20261013T102851Z`: 7/7 probes rc=0, `VERIFY OK`, CRIT 0 / WARN 10 / UNKNOWN 0 / INFO 4 / OK 24).

## 7. Recommendation

Proceed with option C as OpenSpec change `add-intel-nuc-weekly-sentinel`. Remediations the sentinel
reports (log rotation, VHDX compaction, ClickHouse TTL, zookeeper autopurge, sshd password auth,
DB port exposure, loki health) stay owner decisions and get their own cards.
