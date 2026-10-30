# Implementation Review — Vaultwarden Credential Resolution for Synology Release Publisher

**Change:** `synology-publisher-vaultwarden-credentials`  
**Reviewer:** `reviewer` (independent `Verify: agent` pass)  
**Date:** 2026-10-30  
**Verdict:** **APPROVED**

## Executive Summary

The implementation satisfies the revised Vaultwarden credential-ladder delta. It adds `username` reference support to the plugin, resolves publisher credentials independently per variable with authoritative and opportunistic Rung-2 behavior, keeps fixtures isolated from host configuration and binaries, forwards the Python seams through CLI and PowerShell, and guards standalone/plugin grammar drift through a shared corpus.

No blocking Standards or Specification-fidelity findings were identified. The canonical Crew proof was independently run and recorded **PASS**; strict OpenSpec validation and the focused public-seam suite pass. No `apps/desktop` modifications were present.

## Findings Summary

| Severity | Count |
|---|---:|
| CRITICAL | 0 |
| WARNING | 0 |
| INFO | 2 |

## Scope

### Reviewed

- Delta OpenSpec: `proposal.md`, `design.md`, `tasks.md`, and `specs/vaultwarden-credential-ladder/spec.md`.
- Prior proposal report: `reviews/findings-proposal-review.md`.
- Implementation: `_config/agent4070/hermes/plugins/vaultwarden/__init__.py`, `_config/agent4070/hermes/services/synology_release_publisher.py`, and `_config/agent4070/hermes/services/build-desktop-installer.ps1`.
- Tests: `_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py` and `_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`.
- Standards/architecture: `oss/hermes-agent/AGENTS.md`, `plugins/AGENTS.md`, `code-review-mandate/spec.md`, `lessons-learned.md`, and `openspec/workspace/sessions/agent_share.md`.
- SDLC lenses: specification completeness/correctness/coherence; implementation, tests, secret hygiene, PowerShell invocation, deployment seam, and Zero-Checkout state.

### Not Reviewed

- No live `.env`, Vaultwarden session/token, Windows user registry value, host `bw` invocation, DSM/NAS publication, service restart, commit, checkout, merge, or archive operation was performed.
- A full repository regression suite was not run: this card's contract specifies the focused plugin/publisher suite.

## Evidence

### Zero-Checkout and scope

```text
O:/workspaces | branch=main
O:/workspaces/oss/crew | branch=main
O:/workspaces/oss/hermes-agent | branch=main
O:/workspaces/oss/hermes-nerve | branch=main
--- desktop diff ---
--- diff check relevant configuration ---
warning: ... build-desktop-installer.ps1, LF will be replaced by CRLF ...
```

All listed non-primary worktrees were under `O:/workspaces/.worktrees/`; no tracked or untracked `apps/desktop/` change was reported.

### Strict OpenSpec validation

```text
C:/nvm4w/nodejs/openspec.exe validate synology-publisher-vaultwarden-credentials --strict
Change 'synology-publisher-vaultwarden-credentials' is valid
rc=0
```

### Focused implementation suite using the repository virtual environment

```text
O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest \
  O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py \
  O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -q
181 passed, 12 subtests passed in 77.34s (0:01:17)
rc=0
```

### Contract/seam and anti-tautology inspection

```text
PASS | Completeness: per-variable three-rung logic
PASS | Correctness: mapped failures fail closed
PASS | Correctness: fixture paths exclude PATH
PASS | Coherence: public Python/CLI/PowerShell seams
PASS | Anti-tautology: loopback + fake-bw failure matrix
PASS | Grammar parity corpus
PASS | Spec delta references active requirements
rc=0
```

This inspection is grounded in the public production seams (`publish_release_artifacts`, CLI flags, PowerShell parameters) and behavioral fake-`bw` plus loopback-DSM tests—not source-reading tests. The failure matrix and grammar corpus appear at `test_synology_release_publisher.py:932-1055,1151-1229`; the PowerShell forwarding appears at `build-desktop-installer.ps1:27-30,200-204`.

## Findings

No blocking findings. The focused test suite contains public-seam integration coverage (loopback DSM, fake `bw`, subprocess CLI and PowerShell execution) rather than implementation-tautological assertions.

## Required specification-fidelity checks that passed

- Plugin username grammar and extraction: `spec.md:9-22` is implemented by `plugins/vaultwarden/__init__.py:45-47,85-173` and exercised by `test_vaultwarden.py:130-137,312-344`.
- Per-variable Rung 1 → Rung 2 → Rung 3 ladder, mapped authority, partial mapping, and mixed sources: `spec.md:24-57`; production loop at `synology_release_publisher.py:523-567`; behavioral tests at `test_synology_release_publisher.py:817-920`.
- Authoritative fail-closed and opportunistic failure matrix: `spec.md:58-97`; production paths at `synology_release_publisher.py:539-564`; matrix tests at `test_synology_release_publisher.py:932-1022`.
- Hermetic fixture paths and seams: `spec.md:98-115`; `synology_release_publisher.py:523-534,860-878`; PowerShell forwarding at `build-desktop-installer.ps1:27-30,200-204`; test coverage at `test_synology_release_publisher.py:1025-1120,1190-1229`.
- Grammar parity: `spec.md:116-121`; tests at `test_synology_release_publisher.py:1151-1187`.

## Isolated Two-Axis Review

### Standards

- No documented-standard violation was reproduced. The lambda assignment at `synology_release_publisher.py:415` carries an E731 suppression, but it is not a demonstrated ratchet finding; root `AGENTS.md:66-69` forbids using `# noqa` to waive a ratchet finding, not all targeted lint suppressions.
- **INFO — Fowler heuristic:** The standalone parser/resolver duplicates plugin logic (`synology_release_publisher.py:121-520` versus `plugins/vaultwarden/__init__.py:45-260`), a possible Duplicated Code / Shotgun Surgery smell. Design D2 (`design.md:28-36`) explicitly justifies this by bare-stdlib execution and the grammar parity tests constrain drift risk. This is accepted for the stated isolated-runtime boundary.
- **INFO — fallback parser:** The stdlib YAML subset reader is compact and not separately exercised with PyYAML unavailable. Hermetic configuration behavior is covered; targeted no-PyYAML parser stress coverage would be a future hardening improvement, not a demonstrated requirement gap.

### Specification fidelity

No implementation divergence found. The evidence covers Completeness (all delta requirements/seams), Correctness (failure matrix, retry, secret-free errors), and Coherence (narrow public seams with no `apps/desktop` modification). The test design is non-tautological: it executes the public publisher and wrapper through fake external systems rather than restating production formulas or mocking private functions.

## Verdict

**APPROVED.** All card acceptance gates have independent evidence. Task 3.1 is checked by this verifier pass. The two INFO observations are non-blocking and require no source modification for this change.
