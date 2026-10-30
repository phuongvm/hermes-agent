# Proposal Review — Vaultwarden Credential Resolution for Synology Release Publisher

**Change:** `synology-publisher-vaultwarden-credentials`  
**Reviewer:** `reviewer` (independent `Verify: agent` pass)  
**Date:** 2026-10-30  
**Verdict:** **REJECTED**

## Executive Summary

The four OpenSpec artifacts are structurally valid, but the proposed credential contract is not internally coherent for partial Vaultwarden mappings and does not fully define the promised hermetic `bw` seam. These defects are in the normative/specification layer, so implementation must not begin until the artifacts are corrected and revalidated.

| Severity | Count |
|---|---:|
| CRITICAL | 2 |
| WARNING | 2 |
| INFO | 1 |

## Scope

### Reviewed

- `proposal.md`, `design.md`, `tasks.md`
- `specs/vaultwarden-credential-ladder/spec.md`
- Exploration: `openspec/workspace/explorations/2026-10-29-synology-publisher-vaultwarden-credentials-exploration.md`
- Public seams: `plugins/vaultwarden/__init__.py`, `services/synology_release_publisher.py`, `services/build-desktop-installer.ps1`
- Existing publisher and plugin test seams: `plugins/vaultwarden/test_vaultwarden.py`, `services/tests/test_synology_release_publisher.py`
- Applicable architecture/standards: repository `AGENTS.md`, `code-review-mandate/spec.md`, and collaboration lessons.

### SDLC layers evaluated

Specs, architecture/design, existing code/public seams, proposed tests, PowerShell invocation path, hermetic isolation, secret hygiene, and Zero-Checkout state.

## Evidence

- `C:/nvm4w/nodejs/openspec.exe validate synology-publisher-vaultwarden-credentials --strict`
  - Exit code: `0`
  - Raw output: `Change 'synology-publisher-vaultwarden-credentials' is valid`
- Zero-Checkout preflight: `O:/workspaces`, `O:/workspaces/oss/crew`, `O:/workspaces/oss/hermes-agent`, and `O:/workspaces/oss/hermes-nerve` all reported branch `main`; listed non-main Hermes worktrees are under `O:/workspaces/.worktrees/`.
- No `.env` file or secret/session value was read or emitted during this review.

## Findings

### Specifications

#### [CRITICAL] F-01 — Partial explicit mappings have no satisfiable, deterministic credential rule

- **Spec reference:** `specs/vaultwarden-credential-ladder/spec.md:38-40,43-50`; three-rung requirement at lines 24-25.
- **Evidence:** Lines 38-40 allow default `SYNOLOGY_DSM` resolution only if neither credential is mapped. Lines 42-43 make Vaultwarden authoritative and fail-closed when *either* `SYNOLOGY_DSM_USER` or `SYNOLOGY_DSM_PASS` is explicitly mapped. Thus a configuration mapping only one credential cannot resolve the other via the default item or `.env`, while the spec gives no alternative source.
- **Why this blocks:** The proposed implementation cannot infer whether the unmapped variable should use process environment, default Vaultwarden item, `.env`, or fail immediately. The current publisher deliberately supports mixed process/`.env` sources: `services/tests/test_synology_release_publisher.py:395-401` supplies the user from process environment and password from `.env`.
- **Required correction:** Define per-variable resolution and partial-map behavior normatively. State whether an explicit mapping makes only that variable authoritative while the other follows its own ladder, or require both mappings atomically and reject partial configuration with a clear, secret-free diagnostic. Add scenarios/tests for all mixed-source cases.

#### [CRITICAL] F-02 — The claimed hermetic fake-`bw` seam does not specify how host `bw.exe` is excluded

- **Spec reference:** `spec.md:57-62`; proposal lines 14 and 31-33; design lines 31, 38, and 48-49.
- **Evidence:** The normative scenario only requires `<temp_dir>/config.yaml` when `env_file` is supplied (spec lines 60-62). It does not require `bw_binary` to be derived from the fixture or passed explicitly. Yet design line 49 promises Seam B with a fake `bw.cmd`, and the PowerShell design invokes only `-HermesConfigPath`. Current `build-desktop-installer.ps1:196-200` forwards publisher arguments but exposes no binary override.
- **Why this blocks:** A config-only override can still cause the planned resolver to select the live host binary (`<hermes_home>/bin/bw.exe`), contrary to the stated “never read host ... bw.exe” isolation goal. The test could touch host tooling while appearing hermetic.
- **Required correction:** Make binary resolution a normative requirement: add a public PowerShell `-BwBinary` (or equivalent) forwarded as `--bw-binary`, and specify the precedence/fixture derivation rule. Add Seam A and Seam B tests that fail if the host binary path is selected.

#### [WARNING] F-03 — `read_user_env` is required outside the normative testing-seam requirement

- **Spec reference:** proposal line 14, design lines 16 and 32, tasks line 9 versus `spec.md:57-62`.
- **Evidence:** Proposal, design, and task all name an injectable `read_user_env` seam to test `HKCU\Environment`; the sole normative seam requirement names `config_file`, `bw_binary`, CLI flags, and `-HermesConfigPath`, but omits `read_user_env`.
- **Impact:** A conforming implementation can omit the injection seam while still satisfying the delta spec, making Windows retry behavior either non-hermetic or privately monkeypatched.
- **Required correction:** Add `read_user_env` (with its exact signature/behavior) to the normative seam requirement and test scenarios; prefer a name that identifies Windows user-environment reading.

#### [WARNING] F-04 — Opportunistic Vaultwarden fallback cases are underspecified

- **Spec reference:** `spec.md:52-55`; design lines 39-40.
- **Evidence:** The delta specifies fallback when an unmapped vault is *locked*, but does not specify behavior for unmapped missing session, missing executable, invalid config, or configured-server mismatch. Design line 40 discusses only locked/missing default item.
- **Impact:** Implementers can make materially different availability/security decisions under the same OpenSpec contract.
- **Required correction:** Enumerate each Rung-2 failure class and state whether it falls back to Rung 3 or fails closed when mappings are absent. Preserve the explicitly mapped fail-closed rule.

### Architecture / Standards

#### [INFO] F-05 — Duplicated resolver responsibility needs an explicit isolated-runtime rationale and drift tests

- **References:** `design.md:28-35`; `plugins/vaultwarden/__init__.py:85-173,215-260,399-415`; repository `AGENTS.md:35-39,56-59,133`.
- **Evidence:** Design D2 proposes independently reproducing parsing, scope resolution, `bw` invocation, and locked-vault retry inside the standalone stdlib publisher. This is potentially justified because the publisher must run with no Hermes package/PyYAML available, but it creates grammar/behavior drift risk with the plugin.
- **Recommendation:** Retain the standalone implementation only with an explicit design trade-off and a shared behavior matrix in tests for the common `vw://` grammar, scoping, empty username, server mismatch, and retry outcomes. This is advisory, not a blocker once F-01–F-04 are fixed.

## Isolated Two-Axis Review

### Standards (repository rules + Fowler heuristic baseline)

- **Documented-standard concern:** The proposed configuration-only PowerShell seam conflicts with the hermetic isolation promise because it lacks a binary-selection boundary (F-02).
- **Judgement calls:** D2 duplicates substantial resolver behavior across plugin and publisher (possible Duplicated Code / Shotgun Surgery / Divergent Change). The stdlib standalone constraint may justify this; document and test the trade-off rather than silently duplicating behavior.

### Specification fidelity

- The OpenSpec artifacts faithfully carry the broad exploration intent: username support, a three-rung ladder, no secret leakage, and fake-binary tests.
- They do **not** fully translate the exploration’s hermetic test requirement into a deterministic public binary seam, and their partial-map semantics conflict with the existing mixed-source credential behavior (F-01–F-04).

## Not Reviewed

- No new implementation exists; no runtime Vaultwarden, DSM, registry, `.env`, or credential/session value was accessed.
- No live NAS publication, service restart, commit, merge, checkout, or archive operation was performed.
- Canonical `openspec/specs/vaultwarden-credential-ladder/` does not yet exist because this is a proposal-stage change; only the active delta spec was evaluated.

## Required Disposition

**REJECTED.** Revise proposal/spec/design/tasks to resolve F-01 through F-04, add the corresponding normative scenarios and task proofs, then rerun strict OpenSpec validation and request a new independent proposal review. F-05 is advisory but should be incorporated while revising the design.
