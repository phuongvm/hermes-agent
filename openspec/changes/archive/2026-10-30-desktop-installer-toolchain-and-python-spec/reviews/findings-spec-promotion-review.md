# Independent 3D Spec-Promotion Verification

**Change:** `desktop-installer-toolchain-and-python-spec`  
**Reviewer:** `@reviewer`  
**Date:** 2026-10-30  
**Verdict: REJECTED**

## Scope and method

Reviewed the active delta, synced canonical `desktop-release-publishing` specification, both 2026-10-30 exploration documents, the external PowerShell implementation, and its Seam A/Seam B test suite. This review does not archive the change and made no implementation-code edits.

## Evidence executed independently

| Gate | Command | Result |
|---|---|---|
| Delta strict validation | `openspec validate desktop-installer-toolchain-and-python-spec --strict` | `Change 'desktop-installer-toolchain-and-python-spec' is valid` (rc=0) |
| Canonical strict validation | `openspec validate desktop-release-publishing --type spec --strict` | `Specification 'desktop-release-publishing' is valid` (rc=0) |
| Behavioral suite | `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -q` | `190 passed in 130.07s` (rc=0) |
| Zero-Checkout | `git -C <each production checkout> branch --show-current` | `O:/workspaces`, `oss/crew`, `oss/hermes-agent`, and `oss/hermes-nerve` each reported `main` |

The behavioral seam contains public process tests for both areas: `TestSeamBPublisherPython` at `test_synology_release_publisher.py:904` and `TestSeamBToolchainAndDependencyRepair` at `:1675`. The current primary-checkout diff has no `apps/desktop/` change. The expected pre-existing service implementation remains outside this spec-only change (`build-desktop-installer.ps1` modified; publisher and tests untracked in the services tree); `hermes-env.cmd` is not modified.

## 3D assessment

### Completeness

The delta contains two requirements and the canonical specification has nine requirements. It covers the toolchain/dependency repair ladder and the publisher Python selection ladder, including fallback and fail-closed scenarios. The 190-test behavioral suite passed.

### Correctness — blocking finding

- **[HIGH] F-01 — Literal tab corrupts the normative Hermes tools path.**
  - **Evidence:** `grep -nP '\t'` independently reports literal tab characters at delta `specs/desktop-release-publishing/spec.md:6,10,46` and canonical `openspec/specs/desktop-release-publishing/spec.md:160,164,200`.
  - **Impact:** The normative strings render as `<HermesHome>` followed by a tab and `ools` rather than the required literal `<HermesHome>\tools` path. This corrupts the PM-tools fallback contract in both the delta and canonical spec, despite the schema validator accepting it.
  - **Required correction:** Replace each literal tab-derived path with a literal backslash representation appropriate to the Markdown code span (for example, `<HermesHome>\tools`), then rerun strict validation and the verifier review.

### Coherence and scope

The change is deliberately spec-only in the Hermes repository: the tracked diff is limited to `openspec/specs/desktop-release-publishing/spec.md` plus the unrelated shared session log; the change directory is new and remains unarchived. The delta mirrors the externally located implementation and behavioral tests. No unintended source modification under `apps/desktop/` was detected.

## Two-axis isolated review

### Standards

**Result: REJECTED due to F-01.** Literal tab characters are a document-integrity defect in normative filesystem paths. The implementation’s behavioral tests use real `pwsh` subprocess seams and do not rely on source-reading/change-detector assertions. Fowler-style observations about the pre-existing 474-line service script are non-blocking and outside this spec-promotion card.

### Spec fidelity

**Result: REJECTED due to F-01.** Aside from the path corruption, the two requirements faithfully consolidate the two exploration documents: Node >=26/npm >=12 ordered resolution plus fail-closed workspace repair, and Python >=3.10 live-probed selection excluding WindowsApps alias stubs. The canonical addition matches the delta structurally, but both replicate the invalid tab character and thus cannot be accepted as an exact promotion.

## Required rework and re-verification

1. Correct the six literal tab characters in the delta and canonical spec paths.
2. Re-run both strict OpenSpec validations and the full 190-test service suite.
3. Keep the change unarchived; do not alter application or service implementation as part of this documentation correction.
