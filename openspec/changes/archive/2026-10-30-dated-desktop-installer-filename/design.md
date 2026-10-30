# Technical Design: Timestamped Hermes Desktop Installer Filename

## Context

`apps/desktop/scripts/run-electron-builder.mjs` (`validatePreparedBuilderArgs`, line 62) enforces an explicit argument allowlist for prepared Electron builds:
`-c.extraMetadata.version=...`, `-c.directories.output=...`, and `-c.mac.identity=...`. Passing `-c.artifactName=...` fails immediately with `Argument is not admitted by prepared packaging`. Meanwhile, `apps/desktop/` in `O:\workspaces\oss\hermes-agent` must keep zero local git diff so automated upstream merges remain conflict-free.

At the Phase 2 Approval Gate, Commander selected **`Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe`** so neither cross-day nor same-day rebuilds ever overwrite each other locally or on Synology.

## Goals / Non-Goals

### Goals
- Stamp every installer staged by `build-desktop-installer.ps1` as `Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` using the host's local date and 24-hour `HHmm` time captured once at trigger time (or explicit `-BuildStamp <YYYY-MM-DD-HHmm>`).
- Keep `.exe.blockmap` and `latest.yml` filenames/references synchronized with the timestamped `.exe` name so `-IncludeUpdateFeed` works end-to-end.
- Support intuitive `-PushOnly` execution:
  - With `-BuildStamp <S>`: publish exact `Hermes-<Version>-win-x64-<S>.exe`, or stamp `<S>` from undated `Hermes-<Version>-win-x64.exe` if present; fail closed if neither exists.
  - Without `-BuildStamp`: select the newest valid `Hermes-<Version>-win-x64-<YYYY-MM-DD-HHmm>.exe` in `apps/desktop/release/` (so `-SkipPush` followed minutes later by `-PushOnly` publishes the built artifact without duplicating 124 MB), or stamp current `yyyy-MM-dd-HHmm` from undated `Hermes-<Version>-win-x64.exe` when no stamped copy exists yet; fail closed if neither exists.
- Preserve `0` git diff in `O:\workspaces\oss\hermes-agent\apps\desktop\`.

### Non-Goals
- Modifying `electron-builder.config.cjs` or `run-electron-builder.mjs`.

## Decisions

### D1: Post-Build Staging Rename Instead of `-c.artifactName`
- **Decision**: Let `electron-builder` write `release/win-nsis-build/Hermes-<Version>-win-x64.exe` untouched, then stage/copy it to `release/Hermes-<Version>-win-x64-<BuildStamp>.exe` in `build-desktop-installer.ps1`.
- **Rationale**: Respects `run-electron-builder.mjs` `validatePreparedBuilderArgs` and preserves `0` diff in `apps/desktop/`.

### D2: Strict `YYYY-MM-DD-HHmm` Calendar + 24h Clock Validation
- **Decision**:
  - In `build-desktop-installer.ps1`: validate any `-BuildStamp` via `^\d{4}-\d{2}-\d{2}-\d{4}$` AND `[datetime]::ParseExact($stamp, 'yyyy-MM-dd-HHmm', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None)` before packaging or network calls. Capture `(Get-Date).ToString('yyyy-MM-dd-HHmm')` once at script startup.
  - In `synology_release_publisher.py`: validate `build_stamp` (when not `None`) via `re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", build_stamp)` and `datetime.strptime(build_stamp, "%Y-%m-%d-%H%M")`, raising `PublishError` before credential resolution or network probing on any invalid value.

### D3: Scoped `latest.yml` & `.blockmap` Rewrite Helper
- **Decision**: When staging from `win-nsis-build/` (normal build) or stamping from undated `release/Hermes-<Version>-win-x64.exe` (`-PushOnly`), copy `Hermes-<Version>-win-x64.exe.blockmap` (when present) to `Hermes-<Version>-win-x64-<BuildStamp>.exe.blockmap`, and rewrite `latest.yml` (when present) by replacing ONLY the exact active-version token `Hermes-<Version>-win-x64.exe` → `Hermes-<Version>-win-x64-<BuildStamp>.exe` (which also updates `Hermes-<Version>-win-x64.exe.blockmap` → `Hermes-<Version>-win-x64-<BuildStamp>.exe.blockmap` while leaving any foreign-version `0.22.2` references untouched so `_validate_update_feed` still rejects stale `latest.yml` files fail-closed).

### D4: Optional `build_stamp` on Python Seam A (API and CLI), Mandatory `--build-stamp` from PowerShell Seam B
- **Decision**: The CLI flag `--build-stamp` is OPTIONAL (argparse default `None`); omitting it keeps pre-existing undated CLI publication working (regression-tested). `publish_release_artifacts(..., build_stamp: str | None = None)` defaults `build_stamp` to `None` (expecting `Hermes-<version>-win-x64.exe` for existing direct Python tests) and expects `Hermes-<version>-win-x64-<build_stamp>.exe` when `build_stamp` is provided. `build-desktop-installer.ps1` ALWAYS passes `--build-stamp $ResolvedBuildStamp`.

### D5: Deterministic Companion Staging (independent of `-IncludeUpdateFeed`)
- **Decision**: Whenever `build-desktop-installer.ps1` resolves a stamped installer (build or `-PushOnly`), it stages the stamped `.exe.blockmap` and rewrites `latest.yml` to the stamped name if the corresponding undated active-version companions exist and the stamped/rewritten form is not already in place, regardless of whether `-IncludeUpdateFeed` was passed on that run. `-IncludeUpdateFeed` only decides whether the publisher uploads them.
- **Rationale**: A first `-PushOnly` without the flag followed by `-PushOnly -IncludeUpdateFeed` selects the already-stamped `.exe` (it never re-enters the undated fallback), so companion staging must not be coupled to the fallback or the flag; otherwise the stamped installer could not later be published with its feed without manual recovery.
- **Boundary**: `latest.yml` is rewritten only where it still contains the exact undated active-version token. A `latest.yml` already pointing at a different stamp (or a foreign version) is left untouched and the publisher rejects it fail-closed.

### D6: "Latest" Means Latest VALID Stamp
- **Decision**: `-PushOnly` without `-BuildStamp` first filters on-disk candidates to those whose captured stamp passes the D2 calendar + 24h validation, then picks the lexicographic maximum of the remaining set. Impossible stamps (e.g. `9999-99-99-9999`, `2026-02-30-1200`) are never selectable and never mask valid ones.

## Public Testing Seams

1. **Seam A (`synology_release_publisher.publish_release_artifacts` & `main` CLI)**:
   - `build_stamp="2026-10-30-1055"` selects and uploads `Hermes-0.22.4-win-x64-2026-10-30-1055.exe`, ignoring undated, earlier same-day (`2026-10-30-0915`), and foreign-version siblings.
   - `include_update_feed=True, build_stamp="2026-10-30-1055"` validates stamped `latest.yml` and `.blockmap`, rejecting undated or mismatched-stamp `latest.yml`.
   - CLI without `--build-stamp` still publishes the undated `Hermes-<Version>-win-x64.exe` (regression).
   - Invalid `build_stamp` (`"2026-02-30-1200"`, `"2026-10-30-2500"`, `"2026-10-30"`, `"../2026-10-30-1055"`, `""`) raises `PublishError` with `0` HTTP requests.
2. **Seam B (`pwsh.exe -NoProfile -File build-desktop-installer.ps1`)**:
   - Default build (no `-BuildStamp`) produces and uploads `Hermes-<Version>-win-x64-<yyyy-MM-dd-HHmm>.exe`.
   - Same-day rebuilds (`-BuildStamp 2026-10-30-0915` then `-BuildStamp 2026-10-30-1430`) keep both stamped `.exe` files locally and on FakeDSM while uploading only the active run's stamped `.exe`.
   - `-IncludeUpdateFeed` rewrites `latest.yml` and `.blockmap` for the active version and passes strict publisher validation, while still failing closed when `latest.yml` has a stale version (`0.22.2`).
   - `-PushOnly` selects the latest existing stamped installer when `-BuildStamp` is omitted, stamps from undated `Hermes-<Version>-win-x64.exe` when no stamped copy exists yet, ignores impossible on-disk stamps (e.g. `9999-99-99-9999`) while selecting the latest valid one, supports `-PushOnly` followed by `-PushOnly -IncludeUpdateFeed` (companions staged deterministically), honors explicit `-BuildStamp`, and fails closed when neither exists or when `-BuildStamp` is invalid.
