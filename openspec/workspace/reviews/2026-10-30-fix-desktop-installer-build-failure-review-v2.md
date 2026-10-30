# Independent Round-2 Review — Desktop Installer PM Toolchain Fix

- **Card:** `t_926faa0b`
- **Reviewed remediation:** `t_132a2b3d`
- **Implementation:** `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`
- **Tests:** `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`
- **Contract source:** `openspec/workspace/explorations/2026-10-30-fix-desktop-installer-build-failure-exploration.md` §§3–5
- **Prior findings:** `openspec/workspace/reviews/2026-10-30-fix-desktop-installer-build-failure-review.md`
- **Verdict:** **APPROVED**

## Summary

| Severity | Count |
|---|---:|
| CRITICAL | 0 |
| WARNING | 0 |
| INFO | 1 |

The sole blocking Round-1 defect is closed. Every non-`PushOnly` execution now unconditionally enters the three-rung toolchain ladder, and the new public process seam proves the legacy environment script may be absent, no explicit override is required, and an ambient Node 22 placed first on `PATH` is not used. The rejected source-reading change-detector test has been removed.

## Scope

### Reviewed

- Exploration contract, §§3–5 (toolchain ladder, dependency repair, public seams, and scope).
- Prior review findings F-01 / INFO source-test observation.
- Entire `build-desktop-installer.ps1` (415 lines), including toolchain resolution, dependency repair, and non-`PushOnly` dispatch.
- Entire `test_synology_release_publisher.py` (1,682 lines), with focused examination of `bsandbox`, `tsandbox`, and `TestSeamBToolchainAndDependencyRepair`.
- Existing repository contribution/testing standards in `AGENTS.md` and `CONTRIBUTING.md`.
- Collaboration reviewer mandate and lessons: `code-review-mandate/spec.md` and `lessons-learned.md`.
- Current diffs and protected-path state.

### Not Reviewed

- Real desktop packaging or Synology publication: intentionally not run because they would modify the live checkout and/or perform an external operation, outside this read-only review.
- Credentials, `.env` content, live Vaultwarden/Synology state, and service restarts: not accessed.
- No active change delta exists for this targeted services repair; the exploration is the accepted behavior contract.

## Closure of Prior Findings

| Prior finding | Result | Evidence |
|---|---|---|
| CRITICAL: `hermes-env.cmd` condition bypassed the PM ladder | **Closed** | `build-desktop-installer.ps1:314–316` now executes `Initialize-HermesToolchain $TargetRoot` directly inside `if (-not $PushOnly)`, before `Assert-DesktopDependencies`; no `Test-Path $EnvCmd` guard remains. This matches exploration §3 lines 68–76. |
| Missing absent-env / ambient-Node process seam | **Closed** | `test_synology_release_publisher.py:1568–1580` asserts absent `hermes-env.cmd`, omits `-ToolchainBinDir`, prepends fake Node 22 to `PATH`, executes the process seam, and asserts the only recorded Node is `node-26.12.1-win32-x64`. This matches exploration §4 lines 99–105. |
| Source-string change detector was not acceptance proof | **Closed** | `test_script_has_no_hardcoded_nvm_node` has no occurrence. The current seam invokes a copied script as an external PowerShell process (`:660`, `:688`) and checks observed executable selection, not source literals. |

## Independent Evidence

```text
O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest \
  O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -q
184 passed in 123.37s (0:02:03)
PYTEST_RC=0

PowerShell AST parser
PS_PARSE_ERRORS=0

Protected-path checks
git -C O:/workspaces/oss/hermes-agent diff --exit-code -- apps/desktop
APPS_DESKTOP_RC=0
git -C O:/workspaces diff --exit-code -- _config/agent4070/hermes/services/hermes-env.cmd
HERMES_ENV_RC=0
```

### Zero-Checkout Gate

```text
O:/workspaces branch=main
O:/workspaces/oss/crew branch=main
O:/workspaces/oss/hermes-agent branch=main
O:/workspaces/oss/hermes-nerve branch=main
```

`git worktree list` shows feature branches under `O:/workspaces/.worktrees/`; the production checkouts remain on `main`.

## 3D Specification and Seam Audit

- **Completeness:** The non-`PushOnly` public path always initializes the ladder, and the absent-legacy-file regression is covered.
- **Correctness:** The tested configuration selects PM Node 26.12.1 over ambient Node 22 and exercises dependency repair before build invocation.
- **Coherence:** `Initialize-HermesToolchain` remains the single deep interface for override, PM resolver, and semantic-version fallback. The caller no longer leaks unrelated legacy-file state into toolchain safety.
- **QA / Security / DevOps:** The behavioral seam avoids live credentials and network publication. AST parsing is clean; protected source paths have zero diff; no secret, restart, checkout, commit, or live publisher action was performed.

## Two-Axis Review

### Standards

**PASS.** The standards subreview found no blocking violation. The removal of the source-reading test aligns with `AGENTS.md`'s prohibition on change-detector tests and requirement for behavior-contract tests. One advisory Fowler **Duplicated Code** observation remains: virtual-environment candidate enumeration is repeated at `build-desktop-installer.ps1:108–110`, `215–217`, and `394–397`. It predates/extends beyond this remediation and is non-blocking.

### Spec Fidelity

**PASS.** The remediation meets exploration §3's three-rung ladder and fail-closed dependency-repair requirements, and §4's public seam requirement. It respects §5 scope: no diff under `apps/desktop` or `services/hermes-env.cmd`.

## Final Verdict

**APPROVED.** No CRITICAL or WARNING finding remains. The reviewed Round-2 remediation completely closes the prior unconditional-toolchain and behavioral-regression coverage defects.
