# Independent Code Review — Desktop Installer Version Integrity & Legacy HKLM Cleanup

## Verdict

**REQUEST CHANGES / NOT READY FOR DOWNSTREAM QA.**

The aligned path is mostly implemented and its focused unit suite passes, but the pre-build drift guard does not satisfy the approved escape-hatch contract and does not consistently produce the required single actionable failure line. These are release-integrity defects in the principal control added by this change.

The Reviewer made no implementation fixes.

## Review Context

- Change: `desktop-installer-version-integrity`
- Review task: `t_1a7286e6`
- Implementation scope:
  - `apps/desktop/package.json`
  - `apps/desktop/scripts/run-electron-builder.mjs`
  - `apps/desktop/scripts/write-build-stamp.test.mjs`
  - `apps/desktop/resources/installer.nsh`
- Contract sources:
  - `proposal.md`
  - `design.md` decisions D1–D6
  - `specs/installer-version-integrity/spec.md`
  - `tasks.md`
- Architecture sources:
  - repository `AGENTS.md`
  - `apps/desktop/AGENTS.md`
  - `apps/desktop/DESIGN.md`
  - `website/docs/developer-guide/architecture.md`

A dedicated `code-review-mandate/spec.md` and project `lessons-learned.md` are not present in the repository or shared rules tree. The available `rules/skills/code-review.md`, `rules/skills/code-review-expert.md`, and `rules/guides/templates/lessons-learned.md` were loaded as bounded substitutes.

## Three-Dimension Scorecard

| Dimension | Status | Evidence |
|---|---|---|
| Completeness | **FAIL** | The unit tests cover equal versions, one mismatch, missing/invalid stamp, and the mismatch escape path, but do not exercise `main()` orchestration or prove that the canonical version returned by the bypass reaches `buildElectronBuilderArgs`. No test covers bypass behavior for missing or invalid inputs. |
| Correctness | **FAIL** | With a stamp mismatch and `allowDrift: true`, `assertVersionAlignment` returns canonical `0.21.3`, but `main()` ignores that result and builds arguments from stale stamp `0.20.0`, yielding `-c.extraMetadata.version=0.20.0`. Missing/invalid values still throw under `allowDrift: true`. |
| Coherence | **PARTIAL** | Manifest alignment and the consent-gated NSIS flow follow D1/D2/D3. The drift guard is pure, but its result is disconnected from packaging arguments, so D4 and D6 do not form one coherent version flow. |

## Blocking Findings

### P1 — Escape hatch packages the stale stamp version instead of the canonical version

**Contract**

- Delta spec, “Escape hatch warns but does not block”: when drift is allowed, electron-builder must run and `-c.extraMetadata.version` must equal the `pyproject.toml` version (`spec.md:104-109`).
- Design D4: bypass returns `{ ok: false, version: pyprojectVersion }` (`design.md:121-131`).
- Tasks 2.1 and 5.3 require the canonical value to continue through the builder path.

**Implementation evidence**

- `assertVersionAlignment` returns the canonical value on bypass: `apps/desktop/scripts/run-electron-builder.mjs:32-37`.
- `main()` discards the return value: `apps/desktop/scripts/run-electron-builder.mjs:141-147`.
- `buildElectronBuilderArgs` receives the original stale `stamp`: `apps/desktop/scripts/run-electron-builder.mjs:153-157`.
- Builder metadata is derived from `stamp.version`: `apps/desktop/scripts/run-electron-builder.mjs:95-107`.

**Independent reproduction**

```text
node --input-type=module -e "process.argv[1]='review-probe.mjs'; ..."

{
  "result": { "ok": false, "version": "0.21.3" },
  "warnings": [
    "[run-electron-builder] version drift: package.json=0.21.3 pyproject.toml=0.21.3 install-stamp.json=0.20.0 ..."
  ],
  "versionArgs": [
    "-c.extraMetadata.version=0.20.0",
    "-c.artifactName=Hermes-${version}-${os}-${arch}-2026-09-24.${ext}"
  ]
}
Exit 0
```

**Impact**

The emergency bypass can intentionally continue a build but packages the stale stamp value. This violates the explicit spec and can recreate the version-integrity defect the change is intended to prevent.

**Required remediation**

Wire the alignment result into the builder inputs so bypass mode consistently packages the canonical version across `extraMetadata.version`, build version, and artifact naming. Add an orchestration-level regression test proving the spawned builder arguments use the canonical version under drift.

### P1 — `allowDrift` does not downgrade missing or invalid version violations

**Contract**

- Delta spec: “On any violation” packaging fails; setting `HERMES_DESKTOP_ALLOW_VERSION_DRIFT=1` downgrades “the failure” to a warning, with no other bypass (`spec.md:81-109`).
- Design D4: all three values must be present and valid; `allowDrift === true` converts the throw into a warning and returns canonical (`design.md:121-130`).
- Task 2.1 states that each input is validated and bypass warns/returns canonical.

**Implementation evidence**

- Missing stamp throws before `allowDrift` is checked: `run-electron-builder.mjs:22-26`.
- Invalid package, canonical, or stamp values throw directly from `validateSemVer` before `allowDrift` is checked: `run-electron-builder.mjs:28-30`.

**Independent reproduction**

```text
missing-stamp THREW [run-electron-builder] missing install-stamp.json version — run `npm run build` first
invalid-stamp THREW Unsupported or malformed SemVer version: "abc"
missing-package THREW Invalid version: expected non-empty string, got null
Exit 0 (probe caught and printed each exception)
```

**Impact**

The documented emergency mechanism is only a mismatch bypass, not a version-drift/validation bypass. Operators cannot use it for a missing or malformed stamp even though the approved contract says it downgrades any version-integrity violation.

**Required remediation**

Normalize all missing, invalid, and unequal cases into one actionable diagnostic path. Apply `allowDrift` to that complete violation set, subject to an explicit canonical-version safety rule. Add tests for missing and malformed values with bypass enabled.

### P1 — Read/parse failures happen outside the error boundary and violate the single-line failure contract

**Contract**

- Proposal: missing stamp or unreadable `pyproject.toml` exits non-zero with a single actionable line (`proposal.md:15`).
- Delta spec: every violation prints every source/value plus remediation and never spawns electron-builder (`spec.md:81-98`).

**Implementation evidence**

- `loadDesktopManifestVersion()` and `loadCanonicalVersionForBuilder()` execute before the `try` block: `run-electron-builder.mjs:136-141`.
- Only `assertVersionAlignment()` is caught: `run-electron-builder.mjs:141-151`.
- A canonical-load failure therefore escapes as an unhandled stack trace rather than the required single actionable line.

**Independent helper-path reproduction**

```text
Error: Cannot resolve canonical version: neither pyproject.toml nor hermes_cli/__init__.py provided a valid version at Z:/definitely-missing-review-root
    at resolveCanonicalVersion (...write-build-stamp.mjs:127:9)
    at loadCanonicalVersionForBuilder (...run-electron-builder.mjs:45:10)
    ...
```

**Impact**

The process still fails closed, but operator guidance and the specified output contract are not met. Package JSON parse/read failures have the same uncaught behavior.

**Required remediation**

Place all three loads and validation inside one controlled failure boundary. Emit one actionable diagnostic and exit non-zero before spawn. Add child-process tests that cover unreadable/malformed package, canonical source, and stamp inputs and assert no spawn.

## NSIS Hook Audit

The static implementation matches the approved D2/D3 shape:

- raw `Page custom hermesLegacyHklmPre`: `installer.nsh:3-5`;
- guard for non-CurrentUser, UAC inner instance, and silent mode: `installer.nsh:7-13`;
- HKLM uninstall detection plus display/install-location reads: `installer.nsh:15-20`;
- consent prompt with remove / keep / quit: `installer.nsh:22-25`;
- temporary uninstaller copy and `ExecShellWait "runas"`: `installer.nsh:27-30`;
- exact safety arguments `/allusers /S /KEEP_APP_DATA --updated _?=$R2`: `installer.nsh:30`;
- no `UAC_RunElevated`, `--delete-app-data`, or `--keep-shortcuts` in the new hook;
- success determined by HKLM `UninstallString` re-read: `installer.nsh:31-36`;
- existing `customInstall` body is unchanged below the insertion (`installer.nsh:41-56`).

The bundled electron-builder template confirms that `customPageAfterChangeDir` is inserted between the directory and install-files pages, and that its own uninstall flow uses the same `/S /KEEP_APP_DATA ... --updated _?=<dir>` argument shape.

A fresh `npm run dist:win:nsis` reached electron-builder packaging but failed before makensis because the shared checkout's existing `release/win-unpacked/v8_context_snapshot.bin` was locked (`EBUSY`). Therefore this review does **not** claim fresh NSIS compile success. This is an environment/shared-output blocker, separate from the code defects above, and downstream verification must rerun in an isolated output tree after remediation.

## Scope Audit

The four authorized implementation files have this scoped diff:

```text
apps/desktop/package.json                       |   2 +-
apps/desktop/resources/installer.nsh            |  40 ++++++++
apps/desktop/scripts/run-electron-builder.mjs   |  56 ++++++++++
apps/desktop/scripts/write-build-stamp.test.mjs | 130 +++++++++++++++++++++++-
4 files changed, 225 insertions(+), 3 deletions(-)
```

`apps/desktop/package.json` changes exactly one field (`version: 0.17.6 → 0.21.3`). Scoped `git diff --check` exits 0.

The repository-wide working tree is not confined to four files: `git diff --stat` reports 18 modified tracked files plus multiple untracked paths from concurrent changes. This does not prove scope drift by this candidate in the shared workspace, but it means task 5.5 cannot truthfully be verified from repository-wide `git diff --stat`; only the scoped four-file diff is clean.

## Independent Execution Evidence

### Focused unit suite

```text
cd apps/desktop
npx vitest run scripts/write-build-stamp.test.mjs

Test Files  1 passed (1)
Tests       19 passed (19)
Duration    324ms
Exit        0
```

### Strict OpenSpec validation

```text
openspec validate desktop-installer-version-integrity --strict
Change 'desktop-installer-version-integrity' is valid
Exit 0
```

### Aligned build attempt

```text
cd apps/desktop
npm run dist:win:nsis
```

Observed:

- build stamp regenerated as `0.21.3`;
- drift guard passed and electron-builder arguments included `-c.extraMetadata.version=0.21.3`;
- renderer/electron bundling succeeded;
- electron-builder packaging failed before makensis with `EBUSY` on `release/win-unpacked/v8_context_snapshot.bin`;
- command exit 1.

## Final Recommendation

Do not release the pre-created QA acceptance-matrix task yet. Remediate the three drift-guard findings, add orchestration/negative-path coverage, then perform an independent re-review. After that, QA should run the manual Windows matrix and a clean isolated NSIS build. OpenSpec Group 6 and follow-up 7.1 remain correctly open.