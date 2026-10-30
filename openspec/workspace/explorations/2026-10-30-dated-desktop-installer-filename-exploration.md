# Exploration: Timestamped Hermes Desktop Installer Filename (`Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe`)

> **Date**: 2026-10-30
> **Active Project**: `hermes-agent` (`O:/workspaces/oss/hermes-agent`, `OPENSPEC_ROOT = O:/workspaces/oss/hermes-agent/openspec`)
> **Status**: Finalized for OpenSpec Change `dated-desktop-installer-filename` (Commander selected `YYYY-MM-DD-HHmm` so same-day rebuilds never overwrite each other)

---

## 1. Problem Statement

Currently, `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` produces and publishes `Hermes-<Version>-win-x64.exe` (e.g., `Hermes-0.22.4-win-x64.exe`) with no date/time stamp in the filename. When multiple desktop builds are cut on the same day or across different days while `base_version` remains `0.22.4`, operators cannot distinguish one installer build from another in `apps/desktop/release/` or on Synology (`/volume1/_Archives/inspire-robotics/project_files/Releases`), and newer builds overwrite earlier installers of the same version.

At the Phase 2 Approval Gate, Commander explicitly selected **`Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe`** (local trigger date and 24-hour `HHmm` minute stamp) so neither cross-day nor same-day rebuilds ever overwrite prior installers.

---

## 2. Empirical Root Cause & Architecture Seams

1. **Why `electron-builder` Cannot Emit `-<YYYY-MM-DD-HHmm>` Directly Without Touching `apps/desktop/`**:
   - `O:\workspaces\oss\hermes-agent\apps\desktop\electron-builder.config.cjs` (line 87) defines:
     ```js
     artifactName: `${store ? 'Store-' : ''}${artifactNamePascal}-${version}-${os}-${arch}.${ext}`
     ```
   - `O:\workspaces\oss\hermes-agent\apps\desktop\scripts\run-electron-builder.mjs` (`validatePreparedBuilderArgs`, line 62) enforces a strict CLI flag allowlist:
     ```js
     if (/^(?:-c|--config)\.(?:extraMetadata\.(?:version|shortVersion|shortVersionWindows)|directories\.output|mac\.identity)=/.test(arg)) continue
     throw preparationRequired(`Argument is not admitted by prepared packaging: ${arg}`)
     ```
   - Passing `-c.artifactName=...` throws `Argument is not admitted by prepared packaging`, and modifying `apps/desktop/` is prohibited (`Zero modifications to O:/workspaces/oss/hermes-agent/apps/desktop/`).

2. **Clean Post-Build Staging Seam (Zero `apps/desktop/` Diff)**:
   ```
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ electron-builder (--win nsis -c.directories.output=release/win-nsis-build) │
   │   emits: release/win-nsis-build/Hermes-0.22.4-win-x64.exe                  │
   │          release/win-nsis-build/Hermes-0.22.4-win-x64.exe.blockmap         │
   │          release/win-nsis-build/latest.yml                                 │
   └─────────────────────────────────────┬──────────────────────────────────────┘
                                         │
                                         ▼  Post-Build Timestamp Stamping (PS1)
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ build-desktop-installer.ps1 ($BuildStamp = local yyyy-MM-dd-HHmm)          │
   │   stages -> apps/desktop/release/Hermes-0.22.4-win-x64-2026-10-30-1055.exe │
   │             apps/desktop/release/Hermes-0.22.4-win-x64-2026-10-30-1055.exe.blockmap
   │             apps/desktop/release/latest.yml (rewritten to stamped name)    │
   └─────────────────────────────────────┬──────────────────────────────────────┘
                                         │
                                         ▼  --version 0.22.4 --build-stamp 2026-10-30-1055
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ synology_release_publisher.py                                              │
   │   validates YYYY-MM-DD-HHmm + active stamped artifact (+ latest.yml refs)  │
   │   uploads & verifies -> /_Archives/inspire-robotics/project_files/Releases │
   │                         /Hermes-0.22.4-win-x64-2026-10-30-1055.exe         │
   └────────────────────────────────────────────────────────────────────────────┘
   ```

---

## 3. Solution & Deep Module Interface

### 3.1 `build-desktop-installer.ps1`
- Add optional parameter `[string]$BuildStamp`:
  - Strictly validated when supplied: must match `^\d{4}-\d{2}-\d{2}-\d{4}$` and parse via `[datetime]::ParseExact($BuildStamp, 'yyyy-MM-dd-HHmm', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None)`, failing closed before any build or network call on malformed input.
  - When omitted on a normal build (`-not $PushOnly`): captured once at script startup as `(Get-Date).ToString("yyyy-MM-dd-HHmm")` in the host's local timezone when triggered.
- `-PushOnly` Stamp Resolution Rules (deterministic & zero 124 MB duplication):
  1. **Explicit `-BuildStamp <S>` passed**:
     - Target `$ActiveInstallerName = "Hermes-$InstallerVersion-win-x64-$BuildStamp.exe"`.
     - If `$ReleaseDir\$ActiveInstallerName` exists, publish it directly.
     - Else if undated `$ReleaseDir\Hermes-$InstallerVersion-win-x64.exe` exists, copy/stamp it to `$ReleaseDir\$ActiveInstallerName` (and stage stamped `.blockmap` + rewritten `latest.yml` if undated feed files exist) and publish `$ActiveInstallerName`.
     - Else fail closed (never silently pick a different stamp when `-BuildStamp` was explicitly requested).
  2. **`-BuildStamp` omitted on `-PushOnly`**:
     - Inspect `$ReleaseDir` for existing valid stamped files matching `^Hermes-$([regex]::Escape($InstallerVersion))-win-x64-(\d{4}-\d{2}-\d{2}-\d{4})\.exe$` (where the captured group parses as a valid `yyyy-MM-dd-HHmm` calendar timestamp).
     - If one or more exist, select the **latest** stamped file (highest lexicographic `yyyy-MM-dd-HHmm` stamp, e.g. from a prior `-SkipPush` build) and publish that exact `$BuildStamp` without duplicating the 124 MB file.
     - Else if undated `$ReleaseDir\Hermes-$InstallerVersion-win-x64.exe` exists, capture current trigger timestamp `(Get-Date).ToString("yyyy-MM-dd-HHmm")`, copy/stamp `$ReleaseDir\Hermes-$InstallerVersion-win-x64.exe` → `$ReleaseDir\Hermes-$InstallerVersion-win-x64-$BuildStamp.exe` (plus `.blockmap` and active-version `latest.yml` rewrite when present), and publish it.
     - Else fail closed with an actionable message instructing the operator to run a build first.
- Standard Build Staging (`-not $PushOnly`):
  - Require `$BuildOutputDir\Hermes-$InstallerVersion-win-x64.exe` after `electron-builder` succeeds; copy to `$ReleaseDir\Hermes-$InstallerVersion-win-x64-$BuildStamp.exe`.
  - Copy `.blockmap` if present to `$ReleaseDir\Hermes-$InstallerVersion-win-x64-$BuildStamp.exe.blockmap`.
  - Rewrite `latest.yml` if present by replacing ONLY exact active-version token `Hermes-$InstallerVersion-win-x64.exe` → `Hermes-$InstallerVersion-win-x64-$BuildStamp.exe` (so foreign-version `latest.yml` files remain detectable and fail closed in the publisher).
- Publisher Invocation:
  - Always pass `--build-stamp $ResolvedBuildStamp` to `synology_release_publisher.py`.

### 3.2 `synology_release_publisher.py`
- Extend `publish_release_artifacts(..., build_stamp: str | None = None)` and CLI `--build-stamp <YYYY-MM-DD-HHmm>`:
  - Validate `build_stamp` when provided: must match `^\d{4}-\d{2}-\d{2}-\d{4}$` and parse via `datetime.strptime(build_stamp, "%Y-%m-%d-%H%M")`, raising `PublishError` before any credential or network I/O when invalid.
  - Expected active installer:
    - `f"Hermes-{version}-win-x64-{build_stamp}.exe"` when `build_stamp` is set.
    - `f"Hermes-{version}-win-x64.exe"` when `build_stamp` is `None` (backward compatibility for direct Python callers omitting `build_stamp`).
  - `_select_artifacts` and `_validate_update_feed` enforce that only the active `(version, build_stamp)` artifact (and matching stamped `.blockmap` + `latest.yml` when `include_update_feed=True`) is uploaded.

---

## 4. Public Testing Seams

- **Seam A (`publish_release_artifacts` & `main` CLI)**:
  - `build_stamp="2026-10-30-1055"` / `--build-stamp 2026-10-30-1055` uploads `Hermes-0.22.4-win-x64-2026-10-30-1055.exe`, ignoring undated `Hermes-0.22.4-win-x64.exe`, earlier same-day `Hermes-0.22.4-win-x64-2026-10-30-0915.exe`, and stale `Hermes-0.22.2-win-x64-2026-10-30-1055.exe`.
  - `include_update_feed=True, build_stamp="2026-10-30-1055"` validates stamped `latest.yml` and `.blockmap`, rejecting undated or mismatched-stamp `latest.yml`.
  - Invalid `build_stamp` (`"2026-02-30-1200"`, `"2026-10-30-2500"`, `"2026-10-30"`, `"../2026-10-30-1055"`, `""`) raises `PublishError` with `0` DSM network requests.
- **Seam B (`pwsh.exe -NoProfile -File build-desktop-installer.ps1`)**:
  - Default build (no `-BuildStamp`) stages and uploads `Hermes-<Version>-win-x64-<yyyy-MM-dd-HHmm>.exe`.
  - Same-day rebuilds (`-BuildStamp 2026-10-30-0915` then `-BuildStamp 2026-10-30-1430`) keep BOTH stamped `.exe` files side-by-side locally in `release/` and remotely on FakeDSM without overwriting each other.
  - `-PushOnly` without `-BuildStamp` selects the latest valid stamped `Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` in `release/` (e.g. after `-SkipPush`), or stamps from undated `Hermes-<Version>-win-x64.exe` when no stamped copy exists yet; fails closed when neither exists.
  - `-PushOnly -BuildStamp <S>` publishes exact `<S>` (or stamps `<S>` from undated `Hermes-<Version>-win-x64.exe`), and fails closed if only a *different* stamp exists with no undated source.

---

## 5. Out of Scope

- Modifying any file under `O:\workspaces\oss\hermes-agent\apps\desktop\` outside `release/`.
