# intel-nuc-sentinel Specification

## ADDED Requirements

### Requirement: Read-only collection
The sentinel MUST collect every metric through a fixed probe catalog and MUST NOT change state on the
host. Enforcement MUST fail closed: every probe script MUST be accepted by a per-transport allowlist
(bash: only `## name` section blocks whose simple commands use allowlisted executables and arguments;
SQL: only `SELECT` statements calling allowlisted functions; PowerShell: only allowlisted cmdlets, type
literals, static members and methods). A command the allowlist does not know MUST be refused, not run.
A mutating-pattern denylist MUST additionally check probe scripts and remote command lines. Both checks
MUST run before any connection is opened.

#### Scenario: Catalog edit introduces a mutating command
- GIVEN a probe script containing `docker restart`, `rm`, `apt update`, `wsl --shutdown`, SQL write statements (including `MERGE INTO`, `UPDATE SET`, `INSERT`, `DELETE`), Redis writes (including `redis-cli SET`, `DEL`, `HSET`, `LPUSH`), interpreter escape paths (including `python -c`, `perl -e`, `node -e`, `open(...)`), mutating PowerShell cmdlets, or output redirection to a file
- WHEN the configuration or probe is loaded
- THEN loading MUST fail with `ReadOnlyViolation` and the CLI MUST exit 2 without running `ssh` or any runner calls

#### Scenario: Unknown command with the denylist disabled
- GIVEN the mutating-pattern denylist is empty
- AND a probe script uses a command the allowlist does not know (an unlisted executable, a Redis verb other than `PING`/`INFO`, a non-`SELECT` SQL statement or unlisted SQL function, an unlisted PowerShell cmdlet, alias, provider path or .NET member, command substitution, or redirection)
- WHEN `sentinel.py plan` or `sentinel.py run` loads the configuration
- THEN it MUST exit 2, make zero runner calls and create no run directory

#### Scenario: Datastore sessions
- GIVEN the Postgres and ClickHouse probes
- WHEN they connect
- THEN Postgres MUST run with `default_transaction_read_only=on` and ClickHouse with `readonly=1`
- AND the probe MUST echo that setting, and the finding MUST be CRIT if it is not in force

#### Scenario: Unsafe configuration value
- GIVEN a config value with shell metacharacters, or an ssh option naming `ProxyCommand`/`LocalCommand`
- WHEN the configuration is loaded
- THEN loading MUST fail with `ConfigError` and the CLI MUST exit 2

### Requirement: Evidence for every finding
Each finding MUST carry a status (CRIT, WARN, UNKNOWN, INFO, OK), a value, and non-empty evidence. Every
evidence item MUST be a verifiable locator plus the exact raw line: `{probe, section, line_no, line}` where
`line` equals line `line_no` of the stored probe stdout (stderr when `section` is `stderr`) and `line_no`
lies inside the cited section (SQL: a row with that tag). Normalised or derived text MUST NOT appear as
evidence. The only exception is `section: status`, which records that a probe or section produced no output
at all, and it MAY back only an UNKNOWN or CRIT finding. Every finding, including OK and INFO findings that
observe the absence of issues, MUST cite the inspected raw lines. The raw output of every probe (argv, script
sha256, rc, stdout, stderr) MUST be stored with the run.

#### Scenario: Weekly run
- GIVEN a reachable host
- WHEN `sentinel.py run --out-dir D` completes
- THEN `D/runs/<UTC stamp>/` MUST contain `raw.json`, `config.json`, `baseline.json`, `findings.json`, `report.md` and `manifest.json` with the sha256 of the other five

#### Scenario: Absence findings cite inspected evidence
- GIVEN a completely healthy host run
- WHEN the findings are evaluated
- THEN every finding (including `DOCKER-RESTARTS`, `DB-CH-LOG-TTL`, `DOCKER-HEALTH`, `DOCKER-LOG-ROTATION`, `DOCKER-OOM`, `SEC-DB-PORTS`, `SEC-DOCKER-PRIV`, `DOCKER-LOG-SIZE`, `DOCKER-RW-LAYER`, `STORAGE-ORPHANS`) MUST have non-empty `evidence` quoting the inspected raw lines
- AND constructing a finding without an evidence line MUST raise, so no placeholder evidence is ever synthesised

#### Scenario: Evidence is verbatim raw output
- GIVEN any evaluated run (healthy, degraded or failed)
- WHEN `sentinel_evidence.evidence_problems(raw, findings)` runs
- THEN it MUST return no problems: every cited line is the raw line at its locator
- AND `sentinel.py verify` MUST report and exit 4 when a stored evidence line differs from its raw line

### Requirement: No estimated values
A metric MUST be reported UNKNOWN, with the failing section and rc, when its probe section failed, is
missing, or exited 0 but does not have the shape its command prints (malformed). The same applies to a SQL
probe whose rows do not match the typed schema (each tag's row count, column count and column types) and to
Windows JSON that does not match its typed schema, nested `disks`, `vhdx` and `firewall` entries included. A
finding MUST declare every source it needs, primary and cross-check alike; if any of them is unusable the
finding MUST be UNKNOWN with `verified` null, never OK/INFO/WARN with an unverified cross-check, and evaluation
MUST NOT raise. It MUST NOT be reported OK, omitted, or filled from a previous run. Collection completeness MUST compare against the expected
probe catalog and expected sections, and `COLLECT-<PROBE>` MUST list `failed_sections`,
`missing_sections` and `malformed_sections`.

#### Scenario: One probe section fails
- GIVEN the `df` section of `linux_host` exits non-zero
- WHEN the run is evaluated
- THEN `STORAGE-DISK-ROOT` MUST be UNKNOWN
- AND `COLLECT-LINUX_HOST` MUST list `df` in `failed_sections`

#### Scenario: Missing or malformed sections
- GIVEN `loadavg` or `nproc` is missing or malformed in `linux_host`
- WHEN the run is evaluated
- THEN `HOST-LOAD` MUST be emitted as UNKNOWN with evidence
- AND `COLLECT-LINUX_HOST` MUST list the missing sections in `missing_sections`

#### Scenario: Any required section dropped, failed or malformed
- GIVEN any one expected section of `linux_host`, `docker_engine`, `docker_vm` or `ubuntu_root` is dropped, exits non-zero, or exits 0 with unparseable content
- WHEN the run is evaluated
- THEN every finding a healthy run emits MUST still be present
- AND no finding derived from that section MUST be OK
- AND `COLLECT-<PROBE>` MUST name the section in `missing_sections`, `failed_sections` or `malformed_sections`

#### Scenario: Cross-check source unavailable
- GIVEN `docker_info`, `data_du`, `vhdx_stat`, the inner `df`, or the whole `windows_host` probe is dropped, failed or malformed
- WHEN the run is evaluated
- THEN every finding that uses it as primary or cross-check source (`DOCKER-LIVENESS`; `DB-ZK-AUTOPURGE`, `DB-PG-SIZE`, `DB-CH-SIZE`; `STORAGE-VHDX-*`; `STORAGE-DISK-MNT-C/D`) MUST be UNKNOWN with `verified` null

#### Scenario: Malformed rc=0 structured payload
- GIVEN a SQL row with the wrong arity or type (e.g. `conn|10`), a duplicated single-row tag, or Windows JSON whose `disks` holds a non-object
- WHEN the run is evaluated
- THEN evaluation MUST NOT raise
- AND `COLLECT-<PROBE>` and every finding derived from that probe MUST be UNKNOWN

### Requirement: Cross-checked metrics
Where a second independent source exists, the finding MUST record whether both agree (`verified`). When
they disagree the report MUST show MISMATCH and keep the primary value; it MUST NOT reconcile them.

#### Scenario: Disk free disagrees between WSL and Windows
- GIVEN `df /mnt/c` and `Win32_LogicalDisk C:` differ by more than 2 GB
- WHEN the run is evaluated
- THEN `STORAGE-DISK-MNT-C.verified` MUST be false

#### Scenario: Pairs covered
- GIVEN a complete run
- THEN disk free (df vs Win32_LogicalDisk), VHDX length (stat vs Get-Item), container count (docker ps vs docker info), Postgres size (pg_database_size vs du) and ClickHouse size (system.parts vs du) MUST each carry a `verified` value

### Requirement: Deterministic and re-verifiable
Evaluation MUST be a pure function of `raw.json`, `config.json` and `baseline.json`. `sentinel.py verify`
MUST recompute file hashes, rebuild probe scripts from the stored config, re-evaluate the raw output, and
exit 4 on any difference.

#### Scenario: Tampered evidence
- GIVEN a run directory whose `raw.json` was edited after the run
- WHEN `sentinel.py verify --run <dir>` runs
- THEN it MUST report `raw.json: sha256 differs from manifest` and exit 4

### Requirement: Coverage areas
The finding set MUST cover:
- docker: liveness, healthcheck, OOM kill, restarts since baseline, json-file logs without `max-size`, writable-layer size, json-log size
- storage: per-filesystem usage, VHDX slack per configured VHDX, unmounted dirs under the volume root, zookeeper autopurge
- datastores: Postgres reachability, connections, long/idle-in-transaction sessions, XID age, sizes and growth; ClickHouse reachability, system log tables without TTL, sizes and growth; Redis PING and persistence status
- host: WSL and Windows memory, load per CPU, Windows reboot pending
- security: effective sshd auth settings, pending security updates and package-list age, unattended-upgrades, reboot-required flag, Defender, firewall profiles, hotfix age, privileged containers / docker.sock mounts, datastore ports published on all interfaces

#### Scenario: Complete run on a reachable host
- GIVEN all seven probes exit 0
- WHEN the run is evaluated
- THEN `findings.json` MUST contain at least one finding in each area `docker`, `storage`, `db`, `host` and `security`
- AND no finding MUST be UNKNOWN

### Requirement: Weekly schedule without core changes
The skill MUST run as a cron pre-run script from `<HERMES_HOME>/scripts/` and MUST exit 0 whenever a
report was produced, so the cron agent receives it. Exit 2 (config refused) and 3 (host unreachable) MUST
be distinct.

#### Scenario: Host unreachable
- GIVEN every probe's ssh exits 255
- WHEN `sentinel.py run` executes
- THEN it MUST exit 3 and name the raw.json path on stderr
