# Process Compliance & Zero-Trust QA Verification Report

**Change**: `desktop-packaged-version-resolution`  
**Task ID**: `t_3d52398d` (Phase 4 / Milestone 4)  
**Date**: 2026-09-24  
**Auditor**: Agent QA (Process Compliance Guardian)  
**Target Repository**: `O:/workspaces/oss/hermes-agent`  
**Candidate Scope**: `apps/desktop/electron/runtime-version.ts`, `apps/desktop/electron/main.ts`, `apps/desktop/electron/runtime-version.test.ts`  

---

## 1. Executive Summary & Verdict

### Final Verdict: **PASS / APPROVED FOR MILESTONE 5 (RE-PACKAGING)**
### Archive Gate: **BLOCKED PENDING MILESTONE 5 TASK 4.4 EVIDENCE**

The candidate implementation of the packaged runtime version resolution ladder satisfies all functional, architectural, and governance requirements:
1. **Packaging-Mode Priority Inversion**: When `isPackaged === true`, bundled `install-stamp.json` takes precedence over local source trees (`updateRoot`), preventing local Python environments (`%LOCALAPPDATA%\hermes\hermes-agent`) from intercepting client version reporting in Remote Mode.
2. **Zero-Probe Invariant**: When a packaged build carries a valid install stamp (`version` + `shortCommit`), the local source tree is neither probed (`existsSync` count = 0) nor read (`readFileSync` count = 0).
3. **Development Mode Preservation**: When `isPackaged === false` or omitted, source-tree-first resolution is strictly preserved.
4. **Production Wiring Containment**: The single production resolver call site in `apps/desktop/electron/main.ts::resolveHermesVersion()` passes `isPackaged: IS_PACKAGED`, exactly confined to the object literal (4 additions, 4 deletions).
5. **Status Bar Derivation**: Traced end-to-end through `main.ts` IPC -> `store/updates.ts` -> `use-statusbar-items.tsx` -> `version-status.ts` (`client v${appVersion}`).

---

## 2. Process Compliance Audit

| Dimension | Standard | Audit Findings | Status |
| :--- | :--- | :--- | :---: |
| **OpenSpec Phase Artifacts** | Complete set: `proposal.md`, `specs/`, `design.md`, `tasks.md` | All 4 artifacts present in `openspec/changes/desktop-packaged-version-resolution/`. New capability `desktop-runtime-version` defines 5 requirements and 13 scenarios with exact WHEN/THEN syntax. Design decisions D1–D5 documented. Tasks broken into 4 distinct phases. | **PASS** |
| **OpenSpec CLI Strict Validation** | `openspec validate <change> --strict` exits 0 | Executed `openspec validate desktop-packaged-version-resolution --strict`. Output: `Change 'desktop-packaged-version-resolution' is valid`. Exit code 0. | **PASS** |
| **Role Boundary Enforcement** | Strict MAS permissions | - **Designer** (`t_69163db8`): Authored specs/design/tasks. Zero application source modifications.<br>- **Coder** (`t_46fc5b39`): Implemented scoped resolver and test suite. Confined `main.ts` diff to `resolveHermesVersion()`. Updated `tasks.md` checkboxes.<br>- **Reviewer** (`t_70a93768`): Conducted independent review and generated `findings-reviewer-v1.md`. Zero code modifications.<br>- **QA** (`t_3d52398d`): Executed independent zero-trust verification and generated this report. Zero code or doc modifications. | **PASS** |
| **Mandate Execution & Traceability** | Do -> Verify -> Evidence -> Complete | Every task 1.1 through 4.3 and 4.5 in `tasks.md` traces to code and test cases. Task 4.4 remains open pending Milestone 5 installer build. | **PASS** |
| **Anti-Deception / Anti-Sycophancy** | Truth over comfort; no hiding defects | Unrelated global Electron baseline failures and the incomplete status of Task 4.4 are surfaced explicitly as caveats. | **PASS** |

---

## 3. Zero-Trust Verification Evidence

### 3.1 Strict OpenSpec Validation
```text
$ openspec validate desktop-packaged-version-resolution --strict
Change 'desktop-packaged-version-resolution' is valid
Exit Code: 0
```

### 3.2 Desktop TypeScript Compilation
Executed across all three TypeScript project configurations in `apps/desktop`:
```text
$ cd apps/desktop && npm run typecheck
> hermes@0.21.3 typecheck
> tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit && tsc -p tsconfig.e2e.json --noEmit
Exit Code: 0
```
- Core Desktop tsconfig (`.`): 0 errors
- Electron main/preload tsconfig (`tsconfig.electron.json`): 0 errors
- E2E Playwright tsconfig (`tsconfig.e2e.json`): 0 errors

### 3.3 Focused Vitest Test Suites
Executed in `apps/desktop`:
```text
$ cd apps/desktop && npx vitest run electron/runtime-version.test.ts src/lib/version-status.test.ts --reporter=verbose

 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(a): loadInstallStamp schema retention and validation > retains valid version, shortCommit, and buildNumber
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(b): Strict boolean dirty checking > strictly preserves boolean true and false, rejecting string truthy/falsy values
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(c): Malformed fields, schemaVersion mismatch, and non-hex short SHA > rejects schemaVersion !== 1
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(c): Malformed fields, schemaVersion mismatch, and non-hex short SHA > rejects non-hex or invalid length short SHA
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(c): Malformed fields, schemaVersion mismatch, and non-hex short SHA > gracefully handles invalid JSON in loadInstallStamp
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > W-3: Explicit test branches for negative/fractional buildNumber, unreadable stamp, clean/dirty output > negative buildNumber is rejected (normalized to null)
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > W-3: Explicit test branches for negative/fractional buildNumber, unreadable stamp, clean/dirty output > fractional buildNumber is rejected (normalized to null)
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > W-3: Explicit test branches for negative/fractional buildNumber, unreadable stamp, clean/dirty output > unreadable existing stamp throws I/O error and falls back gracefully
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > W-3: Explicit test branches for negative/fractional buildNumber, unreadable stamp, clean/dirty output > clean formatted output: "${version} (${shortCommit})"
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > W-3: Explicit test branches for negative/fractional buildNumber, unreadable stamp, clean/dirty output > dirty formatted output: "${version} (${shortCommit}) [DIRTY]"
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(d) & 4.2(e): Packaged seam resolution and fallback ladder > Rung 1: Dev/Source resolution prefers hermes_cli/__init__.py
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(d) & 4.2(e): Packaged seam resolution and fallback ladder > Rung 2: Packaged client-only runtime resolves from installStamp
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(d) & 4.2(e): Packaged seam resolution and fallback ladder > Rung 2: Packaged client-only runtime with dirty stamp appends [DIRTY]
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > 4.2(d) & 4.2(e): Packaged seam resolution and fallback ladder > Rung 3: Ultimate fallback to app.getVersion() when stamp is missing or invalid
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.1: Packaged + valid stamp + local source tree declaring an older version -> stamp wins and source is NOT read
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.2: Packaged + valid dirty stamp -> resolves with [DIRTY] suffix
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.3: Packaged + no stamp + readable source tree -> resolves to source __version__
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.4: Packaged + stamp with null version -> falls through to source tree, then appVersion
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.5: Packaged + stamp with null shortCommit -> falls through to source tree, then appVersion
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.6: Packaged + no stamp + unreadable source + no appVersion -> "0.0.0"
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.7: isPackaged: false with source tree and stamp -> source tree wins (dev order unchanged)
 ✓ |electron| electron/runtime-version.test.ts > Desktop Runtime Version & Install Stamp Resolution (Phases 3 & 4.2) > Packaged runtime version resolution priority ladder (isPackaged) > 3.8: isPackaged omitted with source tree and stamp -> source tree wins (default = dev)
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > labels a current local client with its version and sha detail
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > appends the commit diff when the client is behind
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > shows a count-free update hint when the client count is unknown
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > names the client as one of two versions in remote mode
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > falls back to the sha, then to unknown, when there is no version
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > drops the diff and the sha detail while an apply is in flight
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > leads the tooltip with the apply message while applying
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > labels the backend target distinctly and never claims a client sha
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > falls back to (update) for a backend that cannot count commits
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > prefers the exact commit diff over the generic (update) hint
 ✓ |ui| src/lib/version-status.test.ts > resolveVersionStatus > hides a backend row that has no version at all

Test Files:  2 passed (2)
Tests:       33 passed (33)
Duration:    1.47s
Exit Code:   0
```

Also verified related update store suite `src/store/updates.test.ts`:
```text
$ cd apps/desktop && npx vitest run src/store/updates.test.ts
Test Files:  1 passed (1)
Tests:       65 passed (65)
Exit Code:   0
```

Root-level execution of `npx vitest run apps/desktop/electron/runtime-version.test.ts`:
```text
Test Files:  7 passed (7)
Tests:       106 passed (106 across worktrees)
Exit Code:   0
```

### 3.4 Status Bar Derivation End-to-End Audit
We audited the complete pipeline connecting the main process resolution ladder to the status bar:
1. **Main Process Ladder**:
   - In `apps/desktop/electron/main.ts::resolveHermesVersion()`:
     Calls `resolveHermesVersionLadder` with `{ updateRoot, installStamp: INSTALL_STAMP, appVersion: app.getVersion(), isPackaged: IS_PACKAGED, fsModule: fs }`.
   - When packaged, valid install stamp returns `formatClientVersion(stamp)` (e.g. `0.21.3 (28e38b39)`).
2. **IPC Channel (`hermes:version`)**:
   - `ipcMain.handle('hermes:version')` returns `{ appVersion: resolveHermesVersion(), ... }`.
3. **Store Ingestion**:
   - `apps/desktop/src/store/updates.ts` fetches `hermes:version` and writes to `$desktopVersion` atom.
4. **Shell Hook**:
   - `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx:345-360` consumes `desktopVersion?.appVersion` and supplies it as `version` into `resolveVersionStatus`:
     ```typescript
     const status = resolveVersionStatus({
       applying,
       behind: updateStatus?.behind ?? 0,
       branch: updateStatus?.branch,
       copy,
       remote: connection?.mode === 'remote',
       restarting: updateApply.stage === 'restart',
       sha: updateStatus?.currentSha?.slice(0, 7) ?? null,
       target: 'client',
       updateAvailable: updateStatus?.updateAvailable,
       version: desktopVersion?.appVersion
     })
     ```
5. **Presentation Formatting**:
   - `apps/desktop/src/lib/version-status.ts:80-87`:
     In Remote Mode (`remote === true`), formats label as `copy.clientLabel(named)`.
   - With `i18n/en.ts:3682` (`clientLabel: (v: string) => \`client v\${v}\``), the label renders as:
     `client v0.21.3 (28e38b39)`.
   - In Local Mode, it renders as `v0.21.3 (28e38b39)`.
   - Status bar derivation is 100% verified and coherent.

### 3.5 Working Tree & Diff Containment
```text
$ git diff --stat -- apps/desktop/electron/
 apps/desktop/electron/main.ts                 |   8 +-
 apps/desktop/electron/runtime-version.test.ts | 243 ++++++++++++++++++++++++++
 apps/desktop/electron/runtime-version.ts      |  93 +++++++---
 3 files changed, 317 insertions(+), 27 deletions(-)

$ git diff --check -- apps/desktop/electron/
Exit Code: 0
```
- `apps/desktop/electron/main.ts`: exactly 8 lines touched (+4, -4), strictly confined to `resolveHermesVersion()` and its comment block. Zero impact on unrelated methods or handlers.

---

## 4. Baseline & Environmental Observations

1. **Unrelated Global Electron Baseline Failure**:
   As documented by Reviewer (`findings-reviewer-v1.md`), running the full un-scoped Electron Vitest suite (`npm run test:desktop:platforms`) reports 192 passed / 13 failed files (2,405 passed / 36 failed tests). This is due to environment conditions in the shared checkout:
   - `desktop-electron-pin.test.ts`: `package-lock.json` lockfile has Electron 43.7.3 while manifest pins 40.10.2.
   - Live Electron runner: Electron binary unbuilt/missing in node_modules on this host.
   - POSIX shell assumptions: tests requiring `/bin/sh` or Unix chmod/symlinks on Windows host.
   These issues are orthogonal to this change and do not invalidate the version ladder implementation.
2. **OpenSpec Task 4.4**:
   Task 4.4 specifies:
   `[ ] 4.4 Manual runtime check on the affected Windows host: installed build with %LOCALAPPDATA%\hermes\hermes-agent present shows client v0.21.3 (<sha>) in Remote Mode status bar and matching About panel; record screenshot or IPC payload as evidence`.
   This task is intentionally held open until Milestone 5 (`t_401a6e4b`) re-packages the Windows desktop installer (`Hermes-*.exe`).

---

## 5. Findings & Process Compliance Scorecard

### Findings by Severity

- **CRITICAL**: 0
  - None. Code meets all specification requirements and test contracts.
- **WARNING**: 1
  - **W-01: Change Archive Blocked Pending Milestone 5 Packaging Verification (Task 4.4)**. The OpenSpec change `desktop-packaged-version-resolution` MUST NOT be archived until Milestone 5 (`t_401a6e4b`) completes the installer rebuild and records the runtime verification receipt.
- **INFO**: 1
  - **I-01: Non-blocking ESLint padding warnings in test file**. 4 formatting warnings exist in `runtime-version.test.ts`. They do not affect execution or type safety.

---

## 6. Recommendations & Handoff

1. **Advance Kanban Pipeline**:
   - Mark Milestone 4 QA task `t_3d52398d` **DONE** (`kanban_complete`).
   - Downstream dependent task `t_401a6e4b` (`[desktop] build: re-package desktop installer and verify binary version stamp`) will promote to **READY** for `@coder`.
2. **Post-Packaging Gate**:
   - `@coder` will execute `apps/desktop/scripts/build-desktop-installer.ps1`, verify PE headers and `install-stamp.json`, run the executable, and satisfy OpenSpec Task 4.4.
3. **Commander Sign-off & Archive**:
   - Once Milestone 5 is verified and Task 4.4 is checked off, the change is eligible for Commander archive approval (`/opsx-archive`).
