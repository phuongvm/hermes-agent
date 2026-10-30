# Independent Round-2 3D Spec-Promotion Verification

**Change:** `desktop-installer-toolchain-and-python-spec`  
**Reviewer:** `@reviewer`  
**Date:** 2026-10-30  
**Verdict: APPROVED**

## Scope and independence

This is an independent verification of the Round-2 remediation for prior finding F-01 and of the active, unarchived promotion from the delta specification into the canonical `desktop-release-publishing` specification. The review compared the two exploration documents, delta, canonical specification, external PowerShell implementation, and public process-seam tests. No implementation, secret, service, checkout, commit, publication, restart, or archive operation was performed.

## Direct evidence

| Gate | Exact command / probe | Raw outcome |
|---|---|---|
| Delta strict validation | `openspec validate desktop-installer-toolchain-and-python-spec --strict` | `Change 'desktop-installer-toolchain-and-python-spec' is valid` (rc=0) |
| Canonical strict validation | `openspec validate desktop-release-publishing --type spec --strict` | `Specification 'desktop-release-publishing' is valid` (rc=0) |
| F-01 byte/text probe | Python byte-count plus exact literal check on both spec files | `delta tabs=0 literal_tools=5`; `canonical tabs=0 literal_tools=5`; `delta_promoted_exact=yes` |
| Behavioral public-seam suite | `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -q` | `190 passed in 128.50s (0:02:08)` (rc=0) |
| Zero-Checkout gate | `git -C <production checkout> branch --show-current` for `O:/workspaces`, `oss/crew`, `oss/hermes-agent`, and `oss/hermes-nerve` | Each printed `main` |
| Active-change gate | `test -d openspec/changes/desktop-installer-toolchain-and-python-spec && test ! -d openspec/changes/archive/desktop-installer-toolchain-and-python-spec` | `active=yes archived=no` |

## 3D assessment

### Completeness — PASS

- The delta's two added requirements at `specs/desktop-release-publishing/spec.md:5-67` are promoted exactly into the canonical specification at `openspec/specs/desktop-release-publishing/spec.md:159-221` (`delta_promoted_exact=yes`).
- The first requirement records every exploration-derived toolchain/dependency-repair boundary: ordered override → PM → semantic-version tool fallback, Node/npm minimums, repair union workspaces, `npm ci` fallback, and fail-closed propagation (`delta:5-44`; exploration: `2026-10-30-fix-desktop-installer-build-failure-exploration.md:65-93`).
- The second records all publisher-Python resolution rungs, WindowsApps rejection, live Python >=3.10 probe, and zero-network fail-closed result (`delta:45-67`; exploration: `2026-10-30-fix-publisher-python-windowsapps-alias-exploration.md:66-95`).
- The public process seams cover the specified outcomes without source-reading/change-detector assertions: `TestSeamBToolchainAndDependencyRepair` at `test_synology_release_publisher.py:1675-1790` and `TestSeamBPublisherPython` at `:904-965`. The full suite passed 190/190.

### Correctness — PASS

- **F-01 closed.** Both normative files contain zero literal tab bytes. The required literal `<HermesHome>\tools` path is present five times in each file, including the former corruption locations (delta lines 6, 10, and 46; canonical lines 160, 164, and 200).
- Both the active delta and the canonical specification passed strict OpenSpec validation with rc=0 and no reported warning/error.
- The contract accurately maps to the implementation seams: `Initialize-HermesToolchain` / `Assert-DesktopDependencies` at `build-desktop-installer.ps1:194-274` and `Test-WorkingPython` / `Resolve-PublisherPython` at `:142-187`.

### Coherence and scope — PASS

- The promoted requirements retain a narrow public process seam (`pwsh.exe -File build-desktop-installer.ps1`), make ordered fallback and failure modes observable, and preserve the existing fail-closed design rather than specifying private state.
- The primary repository's tracked change is limited to `openspec/specs/desktop-release-publishing/spec.md`; `git diff --name-only -- apps/desktop` was empty. The reviewed change directory remains active and unarchived.
- The external services checkout reports pre-existing work-tree state for `services/build-desktop-installer.ps1` (modified) and `services/synology_release_publisher.py` / `services/tests/test_synology_release_publisher.py` (untracked). These are the already-existing implementation/test inputs mapped by this documentation-only change; this review made no edits and found no evidence that Round-2 tab remediation altered those code paths. `hermes-env.cmd` is not reported modified.

## Two-axis isolated review

### Standards

**PASS — no documented-standard violation.** The exact canonical/delta promotion and public behavior-contract seams comply with the repository guidance. Two non-blocking Fowler heuristic observations remain: grouping the toolchain ladder and dependency repair is a possible Divergent Change concern, and the explicit workspace list is a possible Primitive Obsession concern. Design decision 1 explicitly justifies the two-requirement grouping; the literal workspace arguments are appropriate normative CLI contract text. Neither is a defect.

### Spec fidelity

**PASS — no missing, partial, or scope-creep requirement.** The delta maps exactly to both exploration documents and implementation behavior. The former F-01 defect is absent in both promotion targets, and the canonical specification exactly contains the delta addition.

## Archive status

The change remains unarchived by design. This approval is a verification receipt only; it does not authorize archive.
