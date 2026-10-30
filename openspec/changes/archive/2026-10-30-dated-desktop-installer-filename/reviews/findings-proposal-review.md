# Independent Proposal Review — `dated-desktop-installer-filename`

**Verdict: REJECTED**

**Review type:** pre-implementation OpenSpec proposal audit  
**Date:** 2026-10-30  
**Change:** `dated-desktop-installer-filename`

## Scope

Reviewed the active delta artifacts:

- `proposal.md`
- `specs/desktop-release-publishing/spec.md`
- `design.md`
- `tasks.md`

Reviewed the decision evidence and current seams:

- `openspec/workspace/explorations/2026-10-30-dated-desktop-installer-filename-exploration.md`
- `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`
- `O:/workspaces/_config/agent4070/hermes/services/synology_release_publisher.py`
- `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`
- repository and Desktop architecture guidance (`AGENTS.md`, `apps/desktop/AGENTS.md`)

No implementation diff exists yet; implementation correctness, runtime packaging, DSM publication, and real credential paths are **NOT REVIEWED**.

## Verification Receipt

| Check | Result | Raw evidence |
|---|---|---|
| Strict OpenSpec schema | PASS | `C:/nvm4w/nodejs/openspec.exe validate dated-desktop-installer-filename --strict` → `Change 'dated-desktop-installer-filename' is valid`, `OPENSPEC_VALIDATE_RC=0` |
| Zero-Checkout gate | PASS | `git -C` reports `main` for `O:/workspaces`, `oss/crew`, `oss/hermes-agent`, and `oss/hermes-nerve`; `git worktree list` reports only isolated branches under `O:/workspaces/.worktrees/` |
| Extra read-only verification | FAIL (confirms F-01) | `...python.exe -m pytest ...test_synology_release_publisher.py --collect-only -q -k "not seam_b"` printed `TestSeamBBuildScript` cases and `TestSeamBVaultwarden` cases, then `126 tests collected ...`, `COLLECT_FILTER_RC=0` |

## Findings Summary

| Severity | Count |
|---|---:|
| CRITICAL | 0 |
| HIGH | 1 |
| WARNING | 3 |
| INFO | 1 |

## Findings

### F-01 — HIGH — Task 1.1 proof selects the very Seam B tests it claims to exclude

**Contract reference:** `tasks.md:3-6` declares Task 1.1 as Seam A and specifies `-k "not seam_b"`.

**Evidence:** The current test classes are named `TestSeamBBuildScript` (`test_synology_release_publisher.py:588-589`) and `TestSeamBVaultwarden` (`:1191`). Pytest matching is case-insensitive but does not invent the underscore; the independent collection command printed all 15 `TestSeamBBuildScript` cases and all 3 `TestSeamBVaultwarden` cases, concluding `126 tests collected`.

**Impact:** The staged Task 1.1 proof cannot isolate Seam A. It runs PowerShell/process tests before Task 2.1, undermining the task dependency and the intended narrow red/green feedback loop.

**Required correction:** Replace the filter with a selector that demonstrably excludes both Seam B classes (for example, `-k "not SeamB"`), then verify its collection output contains no `TestSeamB*` node IDs. Keep Task 2.1 as the full Seam A + Seam B suite.

### F-02 — WARNING — Feed companions are not durable across a later `-IncludeUpdateFeed` push

**Contract reference:** `specs/desktop-release-publishing/spec.md:59-65`; `design.md:15-17,34-35`; exploration `:65-81`.

**Evidence:** The spec says a `-PushOnly` fallback stamps the `.blockmap` and rewrites `latest.yml` only when `-IncludeUpdateFeed` is set (`spec.md:65`). If an operator first runs `-PushOnly` without that flag, the stamped `.exe` exists. A later `-PushOnly -IncludeUpdateFeed` selects that stamped `.exe` under the prior scenario (`:59-61`) rather than re-entering the undated fallback, but its companions have never been staged. The current publisher requires the active `.exe.blockmap` and `latest.yml` when feed publication is enabled (`synology_release_publisher.py:577-587`).

**Impact:** A valid staged installer cannot later be published with the update feed without manual recovery, despite the proposed push-only workflow.

**Required correction:** State a single deterministic policy: either always stage companion artifacts whenever the undated companions exist, or, when `-IncludeUpdateFeed` is later requested and stamped companions are absent, stage them from the undated active-version feed artifacts before invoking the publisher. Add a Seam B process test for this two-command sequence.

### F-03 — WARNING — Optional CLI backward-compatibility is incomplete in the normative spec

**Contract reference:** `proposal.md:16-18`; `spec.md:26-32`; `design.md:37-38`.

**Evidence:** The proposal/design specify that `build_stamp` is optional and defaults to `None` for existing direct Python callers. The requirement describes the omitted-stamp compatibility scenario only as a “direct Python call” (`spec.md:29-32`), although current CLI tests omit `--build-stamp` (`test_synology_release_publisher.py:490-510`).

**Impact:** The implementation could satisfy the stated direct-Python compatibility while making the pre-existing CLI breaking, with no normative scenario or task test preventing it.

**Required correction:** Specify that `--build-stamp` is optional in the CLI with default `None`, and add a CLI regression test proving undated publication remains supported when the flag is omitted.

### F-04 — WARNING — “Latest” selection must explicitly exclude impossible timestamps

**Contract reference:** `spec.md:59-61`; exploration `:71-75`; design `:15-18`.

**Evidence:** The exploration requires the push-only scan to parse each captured timestamp as a valid calendar timestamp before selecting the lexicographic maximum. The normative scenario says only “latest lexicographic” stamped installer (`spec.md:60`) and Task 2.1 does not name a malformed-on-disk-candidate test (`tasks.md:8-11`).

**Impact:** A filename such as `Hermes-<Version>-win-x64-9999-99-99-9999.exe` can sort above valid artifacts if matching is regex-only, contradicting the explored fail-closed contract.

**Required correction:** Require selection from the subset whose captured stamp passes calendar/24-hour validation, and add a Seam B test containing both malformed and valid stamped candidates.

### I-01 — INFO — Two-axis standards review: no blocking documented-standard breach

The proposal remains scoped to the existing PowerShell and publisher seams (`proposal.md:27-31`) and preserves the `apps/desktop/` zero-diff boundary (`spec.md:3-4`, `design.md:25-27`). The independent Standards axis found no hard standards violation. It noted non-blocking design heuristics: repeated stamp parsing across language boundaries and the push-only fallback’s extra staging behavior. These are advisory; F-01 is the approval blocker.

## Two-Axis Review

### Standards

**PASS with advisory smells only.** The design uses the smallest existing seams, maintains fail-closed behavior, and does not widen Hermes core/Desktop scope. The proposed cross-language validation duplication and procedural push-only fallback are judgment calls, not approval blockers.

### Spec Fidelity

**REJECTED.** F-01 proves the required Seam A task gate is incorrectly specified; F-02 through F-04 are unresolved contract/test gaps against the exploration and current publisher behavior.

## Required Disposition

Revise the OpenSpec artifacts to remediate F-01 through F-04, re-run strict validation, then schedule a new independent proposal review. No source implementation should begin under this proposal revision.
