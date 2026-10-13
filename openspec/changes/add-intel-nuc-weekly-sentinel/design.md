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
| Denylist guard **plus** fixed catalog | Catalog is the allowlist; the guard catches mistakes in future edits | Allowlist regex per command: brittle against legitimate pipes and formats |
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
per probe (matched by script bytes). It covers: catalog validity; 19 refused mutating commands and 5
allowed reads; unsafe config values; healthy host => no CRIT/WARN/UNKNOWN and all cross-checks agree;
stopped container => CRIT with evidence; failed section => UNKNOWN; DB unreachable => CRIT; source
disagreement => `verified=False`; risky posture => WARN; restart deltas and container re-create;
determinism; LF bytes on stdin; timeout => rc 124; run dir verifies and detects tampering; baseline
retention; parsers.
