# Architecture & Design: intel-nuc Weekly Sentinel

## 1. Flow

```text
cron (weekly, 0 7 * * 1)
  └─ <HERMES_HOME>/scripts/intel_nuc_sentinel.py      (templates/cron_wrapper.py, runpy)
       └─ skills/devops/intel-nuc-sentinel/scripts/sentinel.py run
            1. load_config(templates/intel-nuc.json)
                 build_probes() -> token checks + assert_read_only on every script
                 remote_command() + ssh_argv() -> assert_read_only on every command line
            2. collect(): for each of 7 probes
                 ssh -o BatchMode=yes <host> <remote reader>  < script (LF bytes on stdin)
                 -> raw.json {argv, script_sha256, rc, stdout, stderr, seconds}
            3. evaluate(): pure functions over raw.json + config.json + baseline.json
                 Ctx (parsed sections) -> check_collection / check_docker / check_storage /
                 check_db / check_host / check_security -> findings.json, report.md
            4. manifest.json (sha256 of the five files); next baseline.json
  └─ cron agent reads report.md (stdout), runs `verify`, summarises per SKILL.md Procedure
```

## 2. Probes and readers

| Probe | Remote reader | Why this reader |
|---|---|---|
| `linux_host` | `bash --noprofile --norc -s` | login distro; sees `/mnt/c`, `/mnt/d` and the VHDX files |
| `windows_host` | `powershell.exe -NoProfile -NonInteractive -Command -` | Windows truth for disks, VHDX length, Defender, firewall, hotfix, reboot |
| `docker_engine` | `bash -s` | docker CLI; selected inspect fields only |
| `docker_vm` | `wsl.exe -d docker-desktop -e sh -s` | only place the Docker data disk and json logs are visible |
| `ubuntu_root` | `wsl.exe -d Ubuntu -u root -e sh -s` | `sudo` needs a password; root is needed to `du` container-owned data and run `sshd -T` |
| `postgres` | `docker exec -i -e PGOPTIONS=... psql -X -q -At -F '|'` | server-enforced read-only transaction |
| `clickhouse` | `docker exec -i clickhouse-client --readonly=1 --multiquery` | server-enforced readonly profile |

Bash probes print `## <section>` / `##rc <section> <rc>` frames so one failing command degrades a
single finding to UNKNOWN instead of failing the run. SQL probes tag their first column.

## 3. Decisions

| Decision | Rationale | Rejected alternative |
|---|---|---|
| Probe scripts on stdin, fixed remote reader | Remote command line stays short and auditable; no quoting of the script through ssh | `ssh host "<long quoted script>"`: fragile quoting, harder to guard |
| Fail-closed per-transport allowlist (`sentinel_guard.py`) **plus** denylist | Round-1 review showed the denylist alone admitted `redis-cli SET`, SQL `MERGE` and `python -c open(...,'w')`. The allowlist parses each transport: bash scripts must be `_section` blocks whose simple commands use an allowlisted executable with allowlisted arguments (`docker` only `ps/info/inspect/system df` and `exec <c> redis-cli PING|INFO [s]`); SQL must be `SELECT` with allowlisted functions and no `INTO`/`SETTINGS`/`FOR`/comments/dollar quotes; PowerShell only allowlisted cmdlets, types, statics and methods, with no `&`, backtick, redirection, here-strings or provider paths. Anything unknown is refused. The denylist stays as a second check | Denylist only: an unlisted command spelling runs. Full shell/SQL/PowerShell parsers: far more code than an 8-probe catalog needs |
| Evidence is mandatory at construction | `finding()` raises without a non-empty evidence line, so a missing citation is a bug caught by tests, never a placeholder; all-clear findings cite the per-container / per-row values they inspected | Empty evidence on healthy findings; synthesising a placeholder line |
| Evidence = locator + exact raw line (`sentinel_evidence.py`) | Round-2 review found 18 findings citing normalised prose (`db health=none`). `Ctx.cite()` resolves a line to its `line_no` inside the cited section of the stored stdout/stderr and raises if it is not there, so derived text cannot be cited. `evidence_problems()` re-checks every locator; `verify` runs it. `section: status` (no output at all) may back only UNKNOWN/CRIT | Free-text evidence; a separate "raw excerpt" field alongside prose |
| Required-source gating (`gated()`) | Each check lists its primary and cross-check sources (sections, structured probes, the ps+inspect join). Any unusable source makes the finding UNKNOWN with the raw lines showing why; the check body runs only on validated input. Round-2 found DOCKER-LIVENESS, DB sizes, ZK and VHDX findings OK/INFO with `verified=None` when the cross-check source failed | Computing with whatever sources survived and leaving `verified=None` |
| Typed payload schemas | `sentinel_parse.conforms()` checks docker ps/inspect/info records, Windows JSON (nested `disks`/`vhdx`/`firewall`) and SQL rows (per-tag count, arity, column regex) before any consumer reads them, so malformed rc=0 output is UNKNOWN instead of `IndexError`/`TypeError` | Key-presence checks; try/except around consumers |
| Completeness against the expected catalog, with shape checks | `expected_sections(cfg)` lists every section each bash probe must print; `sentinel_parse.well_formed` checks each rc=0 section has the shape its command prints; SQL results need their required tags; Windows JSON needs its typed fields; a docker ps row without an inspect record makes per-container docker checks UNKNOWN. Any gap is UNKNOWN plus `COLLECT-<PROBE>` | Checking only sections present in raw output; trusting rc=0 |
| Bytes on stdin | Windows text-mode stdin injected `\r`, breaking every `2>&1` (seen live) | Text mode |
| `{{with index .State "Health"}}` | `.State.Health` errors on containers without a healthcheck (seen live) | `{{if .State.Health}}` |
| Severity in the report, exit 0 on any report | Cron passes stdout to the agent only on success | Non-zero exit on WARN/CRIT would drop the report |
| Baseline keeps sections a run could not read | One failed probe must not reset week-over-week deltas | Overwrite baseline every run |
| Thresholds in config | Owner tunes without code edits; tests read the shipped config | Constants in code |
| Cross-check tolerances: disk 2 GB, VHDX 1 GB, DB size <= du x 1.05 | Two live reads seconds apart on a busy disk; DB-reported size excludes WAL/overhead, so it must not exceed du | Exact equality: false MISMATCH every run |

## 4. Security

- No secrets read: inspect template never touches `Config.Env`; Postgres auth is the container's
  local trust; nothing from `.env`.
- Interpolated config values pass `SAFE_TOKEN` / `SAFE_WIN_PATH`; Windows paths are single-quoted
  for PowerShell; ssh options cannot name `ProxyCommand` / `LocalCommand`.
- Output is written only under `--out-dir` on the agent machine (default `$HERMES_HOME/sentinel/intel-nuc`).

## 5. Testing

`tests/skills/test_intel_nuc_sentinel_skill.py` fakes ssh with a runner that returns canned output
per probe (matched by script bytes). It covers: catalog validity (allowlist and denylist); refused
mutating commands and allowed reads (including Redis SET, SQL MERGE/UPDATE, interpreter/file escapes);
44 allowlist refusals with the denylist disabled, and CLI exit 2 / zero runner calls / no run directory
for unlisted commands with the denylist disabled; non-empty evidence contract across all findings and
`finding()` raising without evidence; every expected section x {dropped, rc != 0, malformed} emitting
UNKNOWN with no finding lost and none OK from the broken section (90 cases); windows_host / postgres /
clickhouse missing or malformed; ps row without inspect record; every evidence line equals the raw line at
its locator across healthy/risky/failed runs and `verify` flags a synthesised line; each Round-2 cross-check
repro (docker_info, data_du, windows_host, vhdx_stat, inner df, epoch) => dependents UNKNOWN with
`verified=None`; 16 malformed SQL/Windows payloads and 5 malformed docker inspect records => UNKNOWN, no exception;
healthy host => no CRIT/WARN/UNKNOWN and all cross-checks agree; stopped container => CRIT with evidence;
failed section => UNKNOWN; DB unreachable => CRIT; source disagreement => `verified=False`; risky posture => WARN;
restart deltas and container re-create; determinism; LF bytes on stdin; timeout => rc 124; run dir verifies
and detects tampering; baseline retention; parsers.
