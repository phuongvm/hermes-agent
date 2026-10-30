# Independent Review — Desktop Installer Node 26 / node-deps Repair

- **Card:** `t_5305d91b`
- **Reviewed implementation:** `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`
- **Reviewed test suite:** `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`
- **Specification source:** `openspec/workspace/explorations/2026-10-30-fix-desktop-installer-build-failure-exploration.md` §§3–5
- **Verdict:** **REJECTED**

## Summary

| Severity | Count |
|---|---:|
| CRITICAL | 1 |
| WARNING | 0 |
| INFO | 1 |

The new toolchain and dependency-repair implementation is broadly coherent, and the complete focused service suite, PowerShell syntax parse, and protected-path scope checks pass. It nevertheless violates the required three-rung toolchain contract: normal non-`PushOnly` builds resolve the Node/npm toolchain only when the unrelated legacy `hermes-env.cmd` happens to exist. When it is absent and no `-ToolchainBinDir` is supplied, the script executes `Assert-DesktopDependencies` and later `npm.cmd` with the ambient toolchain (the originally failing Node 22 path remains possible). The required resolution ladder is therefore not unconditional and the card cannot be approved.

## Scope

### Reviewed

- Exploration/contract: `O:/workspaces/oss/hermes-agent/openspec/workspace/explorations/2026-10-30-fix-desktop-installer-build-failure-exploration.md` §§3–5.
- Main installer-related specification: `openspec/specs/installer-version-integrity/spec.md`.
- Implementation: complete `build-desktop-installer.ps1` (416 lines), including the PM resolver, semantic tool fallback, dependency repair guard, build and publication ordering.
- Tests: complete `test_synology_release_publisher.py`, specifically `TestSeamBToolchainAndDependencyRepair` at lines 1565–1671.
- Architecture/standards context: `AGENTS.md`, `CONTRIBUTING.md`, `code-review-mandate/spec.md`, collaboration architecture, and lessons-learned.
- SDLC layers: Specs, architecture/seam, code, tests, and operational scope controls.

### Not Reviewed

- A real packaging/install/publish run: deliberately not executed because it would write a production checkout and may contact Synology; the card permits read-only verification only.
- Credentials, `.env` files, live Vaultwarden/Synology behavior, and service restarts: not accessed/executed by scope.
- No active change delta exists for this narrow services-script repair; the exploration is the accepted contract source.

## Evidence

### Automated suite and parse gates

```text
O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest tests/test_synology_release_publisher.py -q
184 passed in 123.17s (0:02:03)
exit code: 0

PowerShell AST parser
PS_PARSE_ERRORS=0
exit code: 0

git diff --exit-code -- oss/hermes-agent/apps/desktop
apps_desktop_rc=0

git diff --exit-code -- _config/agent4070/hermes/services/hermes-env.cmd
hermes_env_rc=0
```

### Zero-Checkout gate

Read-only branch/worktree probe reported:

```text
repo_branch=main
root_branch=main
crew_branch=main
nerve_branch=main
O:/workspaces/.worktrees/... [feature branches]
```

The listed feature branches are confined to `O:/workspaces/.worktrees`; no production checkout was switched.

### PM availability probe

The real Hermes PM tools exist and meet the intended minimums:

```text
O:/workspaces/_config/agent4070/hermes/tools/node-26.12.1-win32-x64/node.exe --version
v26.12.1
O:/workspaces/_config/agent4070/hermes/tools/npm-12.0.2-win32-x64/npm.cmd --version
12.0.2
```

The resolver's returned PATH begins with PM npm and node tool directories. This establishes that the desired toolchain is provisioned; it does not repair the conditional invocation defect below.

## Findings

### Code

#### [CRITICAL] Toolchain ladder is skipped when `hermes-env.cmd` is absent

- **Spec reference:** exploration §3, lines 68–76, requires a three-rung toolchain ladder for the desktop installer; §4 line 101 requires selecting Node 26+ over stale Node 22. The ladder is not conditioned on `hermes-env.cmd`.
- **Location:** `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1:316`.
- **Evidence:** the line is:

  ```powershell
  if ($ToolchainBinDir -or (Test-Path $EnvCmd)) { Initialize-HermesToolchain $TargetRoot }
  ```

  `$EnvCmd` is only a legacy services-file path (`line 66`) and is not an input to `Initialize-HermesToolchain` nor to any ladder rung. With neither `-ToolchainBinDir` nor that unrelated file, `Assert-DesktopDependencies` begins at line 317 and the renderer build runs at line 320 using ambient `node`/`npm.cmd`. The new `TestSeamBToolchainAndDependencyRepair` fixture creates `hermes-env.cmd` at test line 1534, so every normal ladder test satisfies the guard and none proves the required file-absent path.
- **Impact:** a normal build in a valid source layout that lacks the legacy file can silently regress to the stale/ambient Node 22 toolchain—the precise incident being remediated—or use another unverified executable.
- **Required resolution:** call `Initialize-HermesToolchain $TargetRoot` unconditionally for every non-`PushOnly` build (the function itself owns rungs 1–3); add a process-seam regression test where `hermes-env.cmd` is absent, `-ToolchainBinDir` is omitted, and PM Node 26/npm 12 are selected.

### Tests

#### [INFO] The source-string test is not the acceptance proof

- **Location:** `test_synology_release_publisher.py:1566–1569`.
- **Evidence:** `test_script_has_no_hardcoded_nvm_node` reads the PowerShell source and asserts literal absence of `nvm`, `v22.22`, and `install:desktop`.
- **Assessment:** this test is a useful guardrail but is a change-detector, not independent behavioral proof. The process-seam tests are the substantive acceptance tests; they need the file-absent fixture above to cover the failing public behavior.

## 3D Seam / Anti-Tautology Audit

- **Completeness:** fails because the toolchain is not initialized across all non-`PushOnly` paths.
- **Correctness:** the tested paths correctly use semantic Node 26 selection, workspace-union `node-deps.mjs`, `npm ci` fallback, and fail-closed post-repair assertion; the uncovered path negates the required guarantee.
- **Coherence:** `Initialize-HermesToolchain` is the correct deep module/interface for this behavior. The outer `hermes-env.cmd` predicate bypasses that interface for an unrelated filesystem condition and leaks ambient PATH behavior into callers.
- **QA/Security/DevOps:** no secret reads, no `apps/desktop` diff, no `hermes-env.cmd` diff, and no live publication/restart occurred during review. The defect is operational: a machine-specific legacy file controls toolchain safety.

## Two-Axis Review

### Standards

Independent standards review found the same high-severity bypass at line 316. It also identified duplicated virtual-environment discovery loops as maintainability debt and a generic repair error message without remediation. Those are non-blocking relative to the ladder bypass.

### Spec Fidelity

Independent spec-fidelity review confirmed that the implemented rungs, version checks, workspace union repair, `npm ci` fallback, and protected path scope match the exploration on the exercised paths. Its conclusion did not evaluate the line-316 conditional as an absent-`hermes-env.cmd` execution path; the direct contract-to-code audit above does and finds it missing.

## Required Rework and Reverification

1. Remove the `Test-Path $EnvCmd` gate from the normal non-`PushOnly` build path; always enter the toolchain ladder.
2. Add a true process-seam regression test for absent `hermes-env.cmd` without `-ToolchainBinDir`, proving PM Node >=26/npm >=12 selection rather than ambient Node.
3. Re-run the complete `tests/test_synology_release_publisher.py` suite, PowerShell syntax parser, protected-path diff checks, and an independent toolchain dry-check before re-review.
