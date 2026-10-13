---
title: "Intel Nuc Sentinel — Weekly read-only health audit of the intel-nuc host"
sidebar_label: "Intel Nuc Sentinel"
description: "Weekly read-only health audit of the intel-nuc host"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Intel Nuc Sentinel

Weekly read-only health audit of the intel-nuc host.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/devops/intel-nuc-sentinel` |
| Version | `0.1.0` |
| Author | Phuong Lambert, Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `devops`, `docker`, `wsl`, `storage`, `security`, `monitoring`, `cron` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Intel NUC Sentinel Skill

Run a weekly, read-only audit of the intel-nuc Docker host (Windows 11 + WSL2 + Docker Desktop)
across container liveness, storage and VHDX slack, datastore telemetry, host resources and
security posture, then report findings with the raw evidence behind each one. It never changes
the host: every remediation is a proposal for the owner, not an action.

## When to Use

- The weekly `cronjob` for the sentinel fires (its pre-run script output is already in context).
- The owner asks for a health, storage or security check of intel-nuc / DESKTOP-01.
- Before and after a maintenance window, to prove what changed.

Don't use for: fixing anything on the host (log rotation, VHDX compaction, prunes, TTLs). Those
need an owner-approved card of their own.

## Prerequisites

- Key-based `ssh` from the agent machine to the host (`BatchMode=yes`; no password prompts).
- On the host: the `docker` CLI in the login distro, `wsl.exe` with the `Ubuntu` and
  `docker-desktop` distros, `powershell.exe`, and the datastore containers named in
  `templates/intel-nuc.json`.
- Python 3.10+ on the agent machine (stdlib only).

## How to Run

Every command goes through `terminal`, from the skill directory:

```bash
python scripts/sentinel.py plan
python scripts/sentinel.py run --out-dir "$HERMES_HOME/sentinel/intel-nuc"
python scripts/sentinel.py verify --run "$HERMES_HOME/sentinel/intel-nuc/runs/<stamp>"
```

`plan` prints every remote command and script without connecting. `run` collects, evaluates and
prints `report.md`. `verify` re-hashes the run files and re-evaluates `raw.json`; it must print
`VERIFY OK`.

Weekly schedule: copy `templates/cron_wrapper.py` to `$HERMES_HOME/scripts/intel_nuc_sentinel.py`
(cron runs only scripts in that folder), then create the job with the `cronjob` tool:
`schedule="0 7 * * 1"`, `script="intel_nuc_sentinel.py"`, `skills=["intel-nuc-sentinel"]`, and a
prompt such as "Summarise the sentinel report for the owner; follow the skill's Procedure."

## Quick Reference

| Need | Where |
|---|---|
| Probe list, what each reads, read-only guarantees | `references/probes.md` |
| Finding IDs and their WARN/CRIT rules | `references/probes.md` § Findings |
| Host paths, containers, thresholds | `templates/intel-nuc.json` (pass another with `--config`) |
| Exit codes | 0 report, 2 config refused, 3 host unreachable, 4 verify mismatch |
| Week-over-week deltas | `<out-dir>/baseline.json` (written after each run) |

## Procedure

1. Get the report. In a cron run it is the pre-run script output. Otherwise run `sentinel.py run`
   with `terminal`. Exit 2 or 3: stop and report the stderr line verbatim; do not retry with
   other flags or invent values.
2. Run `sentinel.py verify --run <run dir>` with `terminal` (the run dir is the last line of
   `run`). Anything but `VERIFY OK` means the evidence is not trustworthy: report that and stop.
3. Read `report.md` (and `findings.json` for full values) with `read_file`. Quote numbers exactly
   as they appear there; every figure in your summary must trace to a finding ID.
4. Classify for the owner:
   - CRIT first, then WARN, then UNKNOWN (a probe failed: say which and why, never estimate).
   - A `MISMATCH` cross-check is its own item: two sources disagree, show both values.
   - INFO and OK only as counts, unless a delta since the baseline is notable.
5. For each CRIT/WARN, propose a remediation with its blast radius (e.g. "compacting
   `docker_data.vhdx` returns ~N GB but stops all containers"). Do not run it. Remediations that
   delete data (orphan dirs, old images) need the owner to name the targets.
6. If the owner wants follow-up work, open a Kanban card for it; this skill's job ends at the report.

## Pitfalls

- A probe section that fails yields UNKNOWN, not OK. Never fill a gap from an older report or memory.
- A cross-checked finding whose second source failed is UNKNOWN too; do not report it from the surviving source.
- Evidence lines are verbatim raw output with a `line_no` locator; quote them as they are, never paraphrase.
- `apt list --upgradable` is only as fresh as the package lists (`SEC-APT-LISTS-AGE`); the
  sentinel never runs `apt update`, which would write to the host.
- VHDX slack is file length minus filesystem used; it is reclaimable only by compaction in a
  maintenance window, which restarts every container. Freeing space inside the VHDX does not
  shrink the file.
- `DOCKER-RESTARTS` compares against the previous run; the first run shows cumulative counts as INFO.
- The default config is specific to this host. Another host needs its own `--config` file, built
  from facts read off that host, not copied values.

## Verification

- `python scripts/sentinel.py plan` exits 0 and lists 7 probes (config and read-only guard pass).
- `python scripts/sentinel.py verify --run <dir>` prints `VERIFY OK`: file hashes match the
  manifest, probe scripts match the catalog, and re-evaluating `raw.json` yields identical findings.
- Unit tests: `scripts/run_tests.sh tests/skills/test_intel_nuc_sentinel_skill.py -q`.
