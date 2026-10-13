# intel-nuc sentinel: probe and finding catalog

Every probe is a fixed script piped over `ssh` to the host and run by one of these readers. The
script text is hashed into `raw.json`; `sentinel.py verify` rebuilds it from the config and
refuses a run whose hash differs.

| Probe | Runs as | Reads |
|---|---|---|
| `linux_host` | `bash -s` in the default WSL distro (ssh login user) | `date`, `/proc/uptime`, `nproc`, `/proc/loadavg`, `/proc/meminfo`, `df -B1 -P` for `linux_fs`, `stat` of each VHDX |
| `windows_host` | `powershell.exe -Command -` (Windows user) | `Win32_OperatingSystem`, `Get-HotFix`, `Get-MpComputerStatus`, `Get-NetFirewallProfile`, `Win32_LogicalDisk`, `Get-Item` length of each VHDX, two reboot-pending registry keys |
| `docker_engine` | `bash -s` (docker CLI) | `docker ps -a --size`, `docker info`, `docker inspect` (selected fields only, never `Config.Env`), `docker system df`, `redis-cli PING / INFO persistence / INFO memory`, zookeeper autopurge env |
| `docker_vm` | `wsl.exe -d docker-desktop -e sh -s` | `df` of the Docker data disk, `du -sk` of every container `*-json.log` |
| `ubuntu_root` | `wsl.exe -d Ubuntu -u root -e sh -s` | `df /`, `du -skx` of the volume root and the datastore dirs, `sshd -T`, `apt list --upgradable` (no `apt update`), apt cache mtime, `20auto-upgrades`, reboot-required flag |
| `postgres` | `psql` with `PGOPTIONS=-c default_transaction_read_only=on` | sizes, connections, long and idle-in-transaction sessions, XID age, recovery state |
| `clickhouse` | `clickhouse-client --readonly=1` | parts bytes per database, `system.*_log` tables without TTL, disks, uptime |

Root is needed inside WSL only because service data is owned by container UIDs; `wsl.exe -u root`
requires no password and changes nothing. The run never uses `sudo`.

## Read-only guarantees

1. Every probe script must pass the fail-closed allowlist in `scripts/sentinel_guard.py` before
   `ssh` starts. A command it does not know is refused, never run:
   - bash (`linux_host`, `docker_engine`, `docker_vm`, `ubuntu_root`): only `## name` section
     blocks; executables `echo test cat grep nproc date df du stat sshd apt docker xargs` with fixed
     argument shapes (`apt list --upgradable`, `sshd -T`, `docker ps|info|inspect|system df`,
     `docker exec <c> redis-cli PING|INFO [section]`, `docker exec <c> sh -c '<echo only>'`); no
     `$(...)`, backticks, redirection other than `2>/dev/null`, or `&`.
   - SQL (`postgres`, `clickhouse`): `SELECT` only, allowlisted functions, no `INTO`, `SETTINGS`,
     `FOR`, comments, dollar or identifier quotes.
   - PowerShell (`windows_host`): allowlisted `Get-*`/pipeline cmdlets, type literals, static
     members and methods; no `&`, `.` invocation, backtick, redirection, here-strings, provider
     paths or dynamic member names.
   The mutating-pattern denylist in `scripts/sentinel_probes.py` (`assert_read_only`) checks the
   same scripts and every remote command line as well. Both run in `Probe.__post_init__`,
   `build_probes` and `load_config`, so `plan`/`run` exit 2 before any connection.
2. Database sessions are read-only at the server: Postgres refuses writes
   (`cannot execute ... in a read-only transaction`), ClickHouse `readonly=1`. The first query of
   each probe echoes the setting, and `DB-PG-UP` / `DB-CH-UP` go CRIT if it is not in force.
3. Config values interpolated into commands must match a strict token pattern; `ProxyCommand` and
   `LocalCommand` ssh options are refused.
4. The run writes only under its `--out-dir` on the agent machine.

## Cross-checks (the `verified` column)

| Finding | Source A | Source B | Agreement rule |
|---|---|---|---|
| `STORAGE-DISK-MNT-C/D` | `df` in WSL | `Win32_LogicalDisk` free bytes | within 2 GB |
| `STORAGE-VHDX-*` | `stat` in WSL | PowerShell `Get-Item` length | within 1 GB |
| `DOCKER-LIVENESS` | `docker ps -a` rows | `docker info` counters | exact |
| `DB-PG-SIZE` | `sum(pg_database_size)` | `du -skx` of PGDATA | A <= B x 1.05 |
| `DB-CH-SIZE` | `sum(system.parts.bytes_on_disk)` | `du -skx` of the data dir | A <= B x 1.05 |

`MISMATCH` means two sources disagree: report it, never pick one.

## Findings

| ID | Area | WARN / CRIT rule (thresholds in `templates/intel-nuc.json`) |
|---|---|---|
| `COLLECT-*` | collection | a probe or one of its sections exited non-zero -> UNKNOWN |
| `DOCKER-LIVENESS` | docker | any container not `running` -> CRIT |
| `DOCKER-HEALTH` | docker | healthcheck `unhealthy` -> WARN |
| `DOCKER-OOM` | docker | `State.OOMKilled` -> CRIT |
| `DOCKER-RESTARTS` | docker | RestartCount grew since baseline -> WARN (INFO on first run) |
| `DOCKER-LOG-ROTATION` | docker | json-file log without `max-size` -> WARN |
| `DOCKER-LOG-SIZE` | storage | a container log >= `container_log_mb_warn` |
| `DOCKER-RW-LAYER` | storage | writable layer >= `container_rw_mb_warn` |
| `STORAGE-DISK-*` | storage | used % >= `disk_used_pct_warn` / `_crit` |
| `STORAGE-VHDX-*` | storage | file length - used inside >= `vhdx_slack_gb_warn` |
| `STORAGE-ORPHANS` | storage | dirs under the volume root no container mounts -> INFO |
| `DB-ZK-AUTOPURGE` | db | `ZOO_AUTOPURGE_PURGEINTERVAL` unset or 0 -> WARN |
| `DB-PG-UP/CONN/TX/XID/SIZE` | db | read-only not in force -> CRIT; conn % >= `pg_conn_pct_warn`; any >1h or idle-in-tx session; XID age >= `pg_xid_age_warn` / `_crit` |
| `DB-CH-UP/LOG-TTL/SIZE` | db | readonly != 1 -> CRIT; no-TTL system log bytes >= `ch_log_no_ttl_gb_warn` |
| `DB-REDIS-<name>` | db | PING != PONG or a failed bgsave / AOF write |
| `HOST-MEM-WSL/WIN`, `HOST-LOAD`, `HOST-REBOOT-WIN` | host | available % <= `mem_available_pct_warn`; load5 per CPU >= `load_per_cpu_warn`; reboot pending |
| `SEC-SSHD` | security | `passwordauthentication yes` or `permitrootlogin yes` -> WARN; empty passwords -> CRIT |
| `SEC-APT-UPDATES`, `SEC-APT-LISTS-AGE`, `SEC-UNATTENDED`, `SEC-WSL-REBOOT` | security | packages pending from `-security`; lists older than `apt_lists_age_days_warn`; unattended-upgrades off; reboot flag |
| `SEC-WIN-DEFENDER/FIREWALL/HOTFIX` | security | AV or RTP off, signature age > `defender_sig_age_days_warn`; a firewall profile off; newest hotfix older than `hotfix_age_days_warn` |
| `SEC-DOCKER-PRIV`, `SEC-DB-PORTS` | security | privileged container or docker.sock mount; datastore port published on all interfaces |

## Run directory

`<out-dir>/runs/<UTC stamp>/` holds `raw.json` (every probe's argv, script hash, rc, stdout,
stderr), `config.json`, `baseline.json` (the input), `findings.json`, `report.md` and
`manifest.json` (sha256 of the other five). `<out-dir>/baseline.json` is the next run's input.
