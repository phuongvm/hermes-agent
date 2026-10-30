# Independent Implementation Review — `dated-desktop-installer-filename`

**Verdict: APPROVED**

**Review type:** independent implementation verification (OpenSpec task 3.1)  
**Date:** 2026-10-30  
**Change:** `dated-desktop-installer-filename`

## Scope and Review Boundary

Reviewed the current implementation and its public behavioral seams:

- `O:/workspaces/_config/agent4070/hermes/services/synology_release_publisher.py`
- `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`
- `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`
- Change artifacts: `proposal.md`, `design.md`, `tasks.md`, and `specs/desktop-release-publishing/spec.md`
- Decision evidence: `openspec/workspace/explorations/2026-10-30-dated-desktop-installer-filename-exploration.md`

The review did not read `.env`, invoke a real DSM endpoint, build an actual Electron installer, restart Gateway/Dashboard, alter implementation source, switch/check out a branch, or commit/merge anything.

## Verification Receipt

| Gate | Result | Raw evidence |
|---|---|---|
| Full behavioral suite | PASS | `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -q` → `228 passed, 12 subtests passed in 108.75s (0:01:48)`; `PYTEST_RC=0` |
| Strict OpenSpec validation | PASS | `C:/nvm4w/nodejs/openspec.exe validate dated-desktop-installer-filename --strict` → `Change 'dated-desktop-installer-filename' is valid`, rc=0 |
| Desktop zero-diff gate | PASS | `git -C O:/workspaces/oss/hermes-agent diff --exit-code -- apps/desktop` → rc=0 (`apps_desktop_rc=0`) |
| Zero-Checkout gate | PASS | `git -C O:/workspaces`, `O:/workspaces/oss/crew`, `O:/workspaces/oss/hermes-agent`, and `O:/workspaces/oss/hermes-nerve` each reported `main`; `git -C O:/workspaces/oss/hermes-agent worktree list` showed non-main branches only under `O:/workspaces/.worktrees/` |
| Extra substantive check: PowerShell parser | PASS | `pwsh.exe -NoProfile -Command ...Parser::ParseFile('.../build-desktop-installer.ps1'...)` → `PS_PARSE_ERRORS=0`, rc=0 |

## OpenSpec 3D Audit

### Completeness — PASS

All implementation tasks are complete after this verification task is checked:

| Task | Evidence |
|---|---|
| 1.1 Publisher timestamp scoping and optional CLI | `synology_release_publisher.py:574-628,809-901`; Seam A tests `test_synology_release_publisher.py:539-627` |
| 2.1 Build/push timestamp staging | `build-desktop-installer.ps1:47-60,142-183,185-253,283-291`; Seam B tests `test_synology_release_publisher.py:726-948` |
| 3.1 Independent verification and desktop zero-diff | This report and the receipts above |

The delta specification contains three requirements and 18 scenarios. The focused test suite exercises each: trigger timestamp staging, same-version coexistence, active-version/stamp scoping, feed-trio validation, invalid stamps before credentials/network/build, both `-PushOnly` resolution modes, impossible on-disk stamp filtering, deterministic companion staging, and propagation of publisher errors.

### Correctness — PASS

- **Fail-closed validation:** PowerShell validates an explicit `-BuildStamp` before target/version resolution or packaging (`build-desktop-installer.ps1:47-60`); Python validates `build_stamp` before artifact lookup, credentials, or endpoint probing (`synology_release_publisher.py:574-584,830-839`).
- **Backward compatibility:** Python `build_stamp` and CLI `--build-stamp` remain optional (`synology_release_publisher.py:809-825,877-897`); direct and CLI omission regressions are behavioral tests (`test_synology_release_publisher.py:593-617`).
- **Active artifact/feed scoping:** `_select_artifacts` selects only the exact active undated/stamped executable and matching companions (`synology_release_publisher.py:587-613`); `_validate_update_feed` rejects mismatched versions and any foreign installer reference before network activity (`:616-628`).
- **Timestamped staging:** `Stage-StampedCompanions` copies a matching blockmap and rewrites only the active undated token (`build-desktop-installer.ps1:151-168`); normal builds copy the active packaged installer to its stamped name (`:221-231`).
- **`-PushOnly` rules:** explicit stamps never substitute another stamped artifact; unstated stamps select only the latest calendar-valid candidate and fall back to stamping an undated active installer; absent artifacts fail closed (`build-desktop-installer.ps1:170-183,232-253`).
- **Late feed publication:** companion staging occurs whenever a stamped installer is resolved, independently of `-IncludeUpdateFeed` (`build-desktop-installer.ps1:144-168,243-247`), verified by the two-command behavioral test (`test_synology_release_publisher.py:925-939`).

### Coherence — PASS

The implementation follows D1 through D6 in `design.md`: it retains electron-builder's original name in `release/win-nsis-build`, stages a timestamped copy post-build, keeps `apps/desktop` unchanged, uses strict equivalent timestamp validation in both language seams, preserves an intentionally small publisher interface, and selects the latest **valid** stamp rather than the lexicographically highest regex match.

The public seam remains deep and localized: `publish_release_artifacts(..., build_stamp=None)` owns artifact/feed validation and publication; `build-desktop-installer.ps1` owns trigger-time capture, staged-file resolution, and forwarding. Tests exercise these public seams through a FakeDSM and isolated PowerShell sandbox rather than source-reading assertions or a real NAS.

## Two-Axis Code Review

### Standards — PASS

No documented-standard breach was found against `O:/workspaces/oss/hermes-agent/AGENTS.md`, including fail-closed behavior, bounded module/function complexity, behavior-contract testing, and the no-desktop-diff boundary. Fowler-smell review found only non-blocking judgments:

- `build-desktop-installer.ps1:244,247` repeats the `Stage-StampedCompanions` call across two branches; this is minor procedural duplication and does not obscure the distinct copy/no-copy branches.
- `synology_release_publisher.py:574-584` represents the cross-process stamp as `str`; a dedicated Python value object would not improve the PowerShell/CLI seam and is not warranted.

### Spec Fidelity — PASS

No missing, partial, incorrect, or out-of-scope behavior was found. Independent spec-fidelity review mapped all 18 delta scenarios to the implementation and behavior tests. The prior proposal-review blockers are substantively closed:

- Task 1.1 now uses `-k "not SeamB"` (`tasks.md:5-6`), avoiding accidental Seam B selection.
- D5/spec scenario require deterministic companion staging and include a two-command regression.
- D4/spec scenario make CLI omission compatibility normative and tested.
- D6/spec scenario require valid-stamp filtering, including malformed-plus-valid candidates.

## Findings

No CRITICAL, HIGH, WARNING, or required remediation findings.

## Final Assessment

**APPROVED.** The implementation satisfies the active OpenSpec delta, preserves the `apps/desktop` zero-diff invariant, passes the complete required behavioral suite, strict schema validation, and the independent PowerShell syntax check. The change is ready for the next governed lifecycle stage; this review does not authorize archival.
