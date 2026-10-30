# Post-Archive Integrity Audit — `desktop-installer-toolchain-and-python-spec`

- **Audit Date**: 2026-10-30
- **Archived Change Path**: `O:\workspaces\oss\hermes-agent\openspec\changes\archive\2026-10-30-desktop-installer-toolchain-and-python-spec`
- **Canonical Spec Path**: `O:\workspaces\oss\hermes-agent\openspec\specs\desktop-release-publishing\spec.md`
- **Crew Pipeline Cards**: `t_5a642b29` (`@coder`), `t_255ac2c6` (`@reviewer` Round 1 `REJECTED` F-01), `t_dc83b870` (`@coder` Round 2 `F-01` remediation), `t_1894c37f` (`@reviewer` Round 2 `APPROVED`)
- **Audit Verdict**: **PASS (`0` data loss, `0` schema warnings, `190/190` tests green)**

---

## 1. Requirement & Scenario Count Verification

| Metric | Archived Delta (`specs/desktop-release-publishing/spec.md`) | Canonical Main Spec (`openspec/specs/desktop-release-publishing/spec.md`) | Check |
|---|---|---|---|
| `### Requirement:` count | `2` ADDED | `9` total (`7` prior + `2` promoted) | **PASS** (`100%` of delta requirements present verbatim) |
| `#### Scenario:` count | `14` ADDED (`9` + `5`) | `44` total (`30` prior + `14` promoted) | **PASS** (`100%` of delta scenarios present verbatim) |
| Line count | `67` lines | `221` lines (`221 >= 67`) | **PASS** |
| Literal tab (`\t`) count | `0` | `0` | **PASS** (`<HermesHome>\tools` literal backslash verified) |
| `openspec validate --strict` | Archived (`--skip-specs -y` `rc=0`) | `Specification 'desktop-release-publishing' is valid` (`rc=0`) | **PASS** |

---

## 2. Implementation & Test Suite Cross-Check

- **Implementation Script**: `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1`
  - `Initialize-HermesToolchain` (`-ToolchainBinDir` -> `source_build_env` -> `<HermesHome>\tools` `node >=26`, `npm >=12`, `git`) and `Assert-DesktopDependencies` (`node-deps.mjs --source <Root> --reuse` over `ui-tui`, `web`, `apps/desktop`, or locked `npm ci` fallback).
  - `Resolve-PublisherPython` + `Test-WorkingPython` + `Find-ManagedPython` (rejects `Microsoft\WindowsApps` stubs, probes Python `>= 3.10` across `HERMES_PYTHON`/`PYTHON` -> `.venv`/`venv` -> `<HermesHome>\tools\python-*` -> `Get-Command python -All`).
- **Automated Regression Suite**: `O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py`
  - `190 passed` (`rc=0`) across Seam A (`FakeDSM` loopback) and Seam B (`pwsh.exe` process sandbox).
- **Live E2E Verification**:
  - Verified by Commander on `2026-10-30 18:32`: built and published `Hermes-0.22.5-win-x64-2026-10-30-1831.exe` (`147,008,257 bytes`) to Synology `/_Archives/inspire-robotics/project_files/Releases` via `https://10.8.0.60:5001` (`exit 0`).
