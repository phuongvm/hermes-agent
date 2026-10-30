# Exploration: Fix Synology Publisher Python Resolution Against Windows Store `WindowsApps\python.exe` Stub (Exit 9009)

- **Date**: 2026-10-30
- **Active Project**: `O:\workspaces\oss\hermes-agent` (`OPENSPEC_ROOT = O:\workspaces\oss\hermes-agent\openspec`)
- **Target Script**: `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1`
- **Target Test Suite**: `O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py`
- **Mode**: `/crew-no-spec` (Defect Exploration via `diagnosing-bugs` + `codebase-design` + `tdd`)

---

## 1. Problem Statement

When an operator runs `.\build-desktop-installer.ps1` from a standard interactive Windows 11 PowerShell 7 window (`PS O:\workspaces\_config\agent4070\hermes\services>`), the desktop build and NSIS packaging now succeed (`Hermes-0.22.5-win-x64-2026-10-30-1641.exe`, `140.2 MB`), but the post-build Synology publication step fails at line 409 with exit code `9009`:

```text
[HERMES BUILD] Publishing Hermes-0.22.5-win-x64-2026-10-30-1641.exe to Synology (/volume1/_Archives/inspire-robotics/project_files/Releases)...
Python was not found; run without arguments to install from the Microsoft Store, or disable this shortcut from Settings > Apps > Advanced app settings > App execution aliases.
Exception: O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1:409
Line |
 409 |              throw "Synology publication failed with exit code $LASTEX …
     |              ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
     | Synology publication failed with exit code 9009
```

---

## 2. Empirical Root Cause

### 2.1 Reproduction Probe & Output

Executed a `< 1s` read-only PowerShell probe simulating a clean interactive Windows 11 user `PATH` (no active Python virtualenv on `PATH`, default `%LOCALAPPDATA%\Microsoft\WindowsApps` present):

```text
FOUND: C:\Users\phuong\AppData\Local\Microsoft\WindowsApps\python.exe | Length=0 | Attributes=Archive, ReparsePoint
HERMES_PYTHON = O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe
PYTHON        = O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe
After setting PATH = source_build_env.PATH, Get-Command python -> C:\Users\phuong\AppData\Local\Microsoft\WindowsApps\python.exe
Python was not found; run without arguments to install from the Microsoft Store, or disable this shortcut from Settings > Apps > Advanced app settings > App execution aliases.
WindowsApps python exit code: 9009
```

### 2.2 Exact Mechanism in `build-desktop-installer.ps1`

1. **`Initialize-HermesToolchain` exports `$env:HERMES_PYTHON` / `$env:PYTHON` without prepending Python's directory to `$env:PATH`**:
   - `hermes_cli.source_build.source_build_env(explicit=False, verify=False)` prepends PM `npm`, `node`, and `git` directories to `PATH` and sets `HERMES_PYTHON = O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe` and `PYTHON = O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe`, leaving `PATH` without a Python Scripts directory when the caller shell had no active virtualenv.
   - And in `-PushOnly` mode (`if (-not $PushOnly)`), `Initialize-HermesToolchain` does not run at all, so neither `HERMES_PYTHON` nor `PATH` is populated.
2. **Lines 391–399 query ambient `Get-Command python` FIRST without probing or filtering `WindowsApps`**:
   ```powershell
   $PyCmd = Get-Command python -ErrorAction SilentlyContinue
   $PyExe = if ($PyCmd) { $PyCmd.Source } else { $null }
   if (-not $PyExe) {
       foreach ($py in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe')) {
           $cand = Join-Path $TargetRoot $py
           if (Test-Path $cand) { $PyExe = $cand; break }
       }
   }
   ```
   - On Windows 11, `C:\Users\phuong\AppData\Local\Microsoft\WindowsApps\python.exe` is a 0-byte Microsoft Store App Execution Alias (`ReparsePoint`) on the default user `PATH`.
   - `Get-Command python` returns `C:\Users\phuong\AppData\Local\Microsoft\WindowsApps\python.exe`, making `$PyExe` non-null and **skipping** the `$TargetRoot\.venv\Scripts\python.exe` loop completely.
   - Invoking `& $PyExe @PubArgs` runs the Store alias stub, which prints `"Python was not found; run without arguments to install from the Microsoft Store..."` and exits with code `9009`.
3. **Why Seam B (`bsandbox`) missed it**:
   - `bsandbox` in `test_synology_release_publisher.py:674` prepended `str(Path(sys.executable).parent)` to `env["PATH"]`, so `Get-Command python` in tests always hit pytest's real `python.exe` ahead of `WindowsApps\python.exe`.

---

## 3. Solution & Deep Module Interface (`Resolve-PublisherPython`)

Replace the fragile inline `$PyCmd = Get-Command python` lookup at lines 391–399 of `build-desktop-installer.ps1` with a deterministic, fail-closed resolver function `Resolve-PublisherPython([string]$Root)` backed by a live interpreter probe `Test-WorkingPython([string]$PyPath)`:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                   Resolve-PublisherPython([string]$Root)                   │
├────────────────────────────────────────────────────────────────────────────┤
│  Gate: Test-WorkingPython($cand)                                           │
│    1. Reject null/empty, non-existent file, or path matching               │
│       `[\\/]Microsoft[\\/]WindowsApps[\\/]` (Store execution alias stub).  │
│    2. Probe: `& $cand -c "import sys; raise SystemExit(0 if                │
│       sys.version_info >= (3, 10) else 1)" 2>$null` -> require rc == 0.    │
├────────────────────────────────────────────────────────────────────────────┤
│  Rung 1: Explicit toolchain / operator env vars                            │
│          `$env:HERMES_PYTHON`, `$env:PYTHON`                               │
│  Rung 2: Target source checkout virtualenvs                                │
│          `$Root\.venv\Scripts\python.exe`, `$Root\venv\Scripts\python.exe` │
│  Rung 3: Hermes PM managed Python under `<HermesHome>\tools`               │
│          `Get-NewestToolDir (Join-Path (Get-HermesHomeDir) 'tools')        │
│           'python' @('python.exe')`                                        │
│  Rung 4: Ambient `PATH` (`Get-Command python -All`)                        │
│          Iterates all `python` candidates on `PATH` in order, returning    │
│          the first candidate that passes `Test-WorkingPython` (skipping    │
│          `WindowsApps` stubs and dead shims).                              │
│  Fail-Closed:                                                              │
│          Throws actionable error listing the checked rungs if no working   │
│          Python >= 3.10 interpreter is found.                              │
└────────────────────────────────────────────────────────────────────────────┘
```

Additionally:
- In `Initialize-HermesToolchain` Rung 3 (`<HermesHome>\tools` fallback), if `$env:HERMES_PYTHON` is not yet set, set `$env:HERMES_PYTHON` / `$env:PYTHON` from the first working checkout venv or `<HermesHome>\tools\python-*\python.exe` so downstream native steps and publisher calls have a consistent `HERMES_PYTHON`.

---

## 4. Public Testing Seams (`TestSeamBToolchainAndDependencyRepair` / `TestSeamBBuildScript`)

All tests run at **Seam B** (invoking real `pwsh.exe -NoProfile -NonInteractive -File build-desktop-installer.ps1` against `bsandbox` / `tsandbox` with `FakeDSM`):

1. **`test_windowsapps_store_alias_stub_on_path_is_skipped_for_venv_python`**:
   - Place a fake `Microsoft\WindowsApps\python.cmd` (or `python.exe` shim) **first** on `PATH` that prints `"Python was not found; run without arguments to install from the Microsoft Store"` to stderr and exits `9009`, with NO other `python` on `PATH`, while providing `<SourcePath>\.venv\Scripts\python.cmd` (wrapping real `sys.executable`).
   - Run both default build and `-PushOnly` against `FakeDSM` -> assert `returncode == 0`, artifact uploaded to `FakeDSM`, and the `9009` stub never aborts publication.
2. **`test_windowsapps_stub_first_on_path_skipped_for_later_working_path_python`**:
   - Place a fake `Microsoft\WindowsApps\python.cmd` (exiting `9009`) **before** the real `python` directory on `PATH`, with no `.venv` in `<SourcePath>` and no `python-*` in `<HermesHome>\tools`.
   - Run `-PushOnly` against `FakeDSM` -> assert `Resolve-PublisherPython` skips the `WindowsApps` stub, finds the working `python` later on `PATH`, and uploads to `FakeDSM` (`returncode == 0`).
3. **`test_hermes_python_env_beats_broken_first_path_python`**:
   - Put a broken non-`WindowsApps` `python.cmd` (exiting `9009`) first on `PATH` and set `HERMES_PYTHON = sys.executable`.
   - Run `-PushOnly` against `FakeDSM` -> assert `returncode == 0` and upload succeeds.
4. **`test_pm_tools_python_used_when_no_venv_and_path_has_only_broken_stub`**:
   - Provision `python-3.14.9-win32-x64` under `<HermesHome>\tools`, remove working `python` from `PATH` (leaving only a `9009` stub), no `.venv` in `<SourcePath>`.
   - Run `-PushOnly` against `FakeDSM` -> assert `returncode == 0` and upload succeeds.
5. **`test_no_working_python_anywhere_fails_closed_before_network`**:
   - Remove working `python` from `PATH` (leaving only a `9009` stub), unset `HERMES_PYTHON`/`PYTHON`, no `.venv`, no `<HermesHome>\tools\python-*`.
   - Run `-PushOnly` against `FakeDSM` -> assert `returncode != 0`, `dsm.request_count == 0`, and error message states `"No working Python"`.

---

## 5. Out of Scope

- Zero modifications to `O:\workspaces\oss\hermes-agent\apps\desktop\`.
- Zero modifications to `O:\workspaces\_config\agent4070\hermes\services\hermes-env.cmd`.
- Zero modifications to `O:\workspaces\_config\agent4070\hermes\services\synology_release_publisher.py`.
