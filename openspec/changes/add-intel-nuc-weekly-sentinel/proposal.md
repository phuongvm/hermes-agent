# Change Proposal: intel-nuc Weekly Sentinel

## Why
intel-nuc (192.168.100.110) hosts 31 production containers across Windows 11, WSL2 and Docker
Desktop. Storage incidents (t_53f11012, t_c57d5ba5) were found by hand, after the fact, and the
fixes from them have no scheduled re-check. The owner asked for a weekly check covering stability
and security as well as storage, run by scripts so that the agent does not guess metrics or run
harmful commands, with every metric backed by evidence. Exploration:
`openspec/workspace/explorations/2026-10-13-intel-nuc-weekly-sentinel-exploration.md`.

## What Changes
- New bundled skill `skills/devops/intel-nuc-sentinel/`:
  - `scripts/sentinel.py`: CLI with `plan | collect | evaluate | run | verify`.
  - `scripts/sentinel_probes.py`: fixed probe catalog, config token validation, read-only guard.
  - `scripts/sentinel_parse.py`: pure parsers for `df`, `du`, `stat`, `meminfo`, `sshd -T`, apt, JSON lines.
  - `scripts/sentinel_checks.py`, `scripts/sentinel_platform.py`: deterministic findings for
    docker, storage, datastores, host and security, with cross-checks.
  - `templates/intel-nuc.json`: host paths, container names, thresholds.
  - `templates/cron_wrapper.py`: pre-run script for a weekly `cronjob`.
  - `references/probes.md`: probe catalog, guarantees, cross-checks, finding rules.
- New tests `tests/skills/test_intel_nuc_sentinel_skill.py` (no network; ssh faked).
- Generated docs: `website/docs/user-guide/skills/bundled/devops/devops-intel-nuc-sentinel.md`,
  a row in `website/docs/reference/skills-catalog.md`, a sidebar entry in `website/sidebars.ts`.

No core code changes. No change to the host.

## Out of scope
- Any remediation (log rotation, VHDX compaction, prunes, ClickHouse TTL, zookeeper autopurge,
  sshd hardening, port binding). The sentinel reports; the owner opens cards.
- Creating the cron job on the live gateway (owner action; steps in SKILL.md).
- Hosts other than intel-nuc (the config is host-specific by design).

## Impact
- Bundled skill count +1 in the devops category.
- Weekly load on the host: 7 ssh sessions, ~12 s wall time measured.
