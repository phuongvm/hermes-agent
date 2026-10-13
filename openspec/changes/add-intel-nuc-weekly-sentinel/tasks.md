# Implementation Tasks: intel-nuc Weekly Sentinel

- [x] Task 1: Probe catalog and read-only guard
  - `skills/devops/intel-nuc-sentinel/scripts/sentinel_probes.py`: 7 probes, `assert_read_only`, token validation, `ssh_argv`.
  - `templates/intel-nuc.json`: host facts read live on 2026-10-13 (exploration §2) and thresholds.
  - Proof: `python skills/devops/intel-nuc-sentinel/scripts/sentinel.py plan` exits 0 and prints 7 `###` probe headers.

- [x] Task 2: Parsers and deterministic checks
  - `sentinel_parse.py`, `sentinel_checks.py`, `sentinel_platform.py`: findings for docker, storage, db, host, security; UNKNOWN on failed sections; cross-checks with `verified`.
  - Proof: `scripts/run_tests.sh tests/skills/test_intel_nuc_sentinel_skill.py -q` passes.

- [x] Task 3: CLI, evidence chain and baseline
  - `sentinel.py`: `plan | collect | evaluate | run | verify`; run dir with raw/config/baseline/findings/report/manifest; baseline retention.
  - Proof: `python skills/devops/intel-nuc-sentinel/scripts/sentinel.py verify --run <run dir>` prints `VERIFY OK`.

- [x] Task 4: Live read-only run against 192.168.100.110
  - Evidence: `O:/workspaces/_config/agent4070/hermes/kanban/boards/crew/workspaces/t_bb397dbc/sentinel/runs/20261013T102851Z` (7/7 probes rc=0, VERIFY OK).
  - Proof: `raw.json` rc column all 0; `report.md` counts line present.

- [x] Task 5: Skill document, reference and cron wrapper
  - `SKILL.md` (authoring standards: description <= 60 chars, modern section order), `references/probes.md`, `templates/cron_wrapper.py`.
  - Proof: `scripts/run_tests.sh tests/skills/test_authoring_standards.py -q` passes.

- [x] Task 6: Generated docs
  - `website/docs/user-guide/skills/bundled/devops/devops-intel-nuc-sentinel.md` (from `website/scripts/generate-skill-docs.py`, POSIX path, LF), catalog row, sidebar entry. Unrelated regeneration noise from running the generator on Windows (CRLF, backslash paths in 214 files) was discarded.
  - Proof: `scripts/run_tests.sh tests/skills/test_skill_docs_contract.py tests/skills/test_skill_pages_match_shipped_skills.py -q` passes.

- [x] Task 7: Remediate Round-1 Reviewer findings (C-01, C-02, C-03)
  - C-01: Comprehensively close mutating classes in `sentinel_probes.py` (`assert_read_only` blocks Redis writes including `redis-cli SET`, SQL write statements including `MERGE INTO` and `UPDATE SET`, file redirection, and scripting runtimes `python -c`, `open(`, `.write(`); add `Probe.__post_init__` check and behavioral CLI tests proving exit 2 and zero runner calls.
  - C-02: Enforce non-empty evidence across all findings in `sentinel_checks.py` and `sentinel_platform.py` (including OK/INFO absence findings `DOCKER-RESTARTS`, `DB-CH-LOG-TTL`, `DOCKER-HEALTH`, `DOCKER-LOG-ROTATION`, `DOCKER-OOM`, `SEC-DB-PORTS`, `SEC-DOCKER-PRIV`, `DOCKER-LOG-SIZE`, `DOCKER-RW-LAYER`, `STORAGE-ORPHANS`); add non-empty evidence contract test.
  - C-03: Implement expected catalog and section completeness check in `check_collection`, cascade missing/malformed sections to UNKNOWN findings across host (`HOST-LOAD`), security, DB, and docker checks; add missing-section and malformed-payload tests.
  - Proof: 67/67 unit tests pass in `test_intel_nuc_sentinel_skill.py`; `sentinel.py verify` passes on live run `20261013T103241Z`.

- [ ] Task 8: Independent re-review (@reviewer / crew-verifier) of skill, scripts and artifacts.

- [ ] Task 9 (owner): Schedule the weekly job
  - Copy `templates/cron_wrapper.py` to `<HERMES_HOME>/scripts/intel_nuc_sentinel.py`; create the job with `cronjob` (`schedule="0 7 * * 1"`, `script="intel_nuc_sentinel.py"`, `skills=["intel-nuc-sentinel"]`). Not done by this card: it changes the live gateway's cron store.
