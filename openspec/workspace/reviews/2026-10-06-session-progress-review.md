# Session Progress and Design Inspection

- **Inspection date:** 2026-10-14
- **Session inspected:** `default/20261006_083443_c97909`
- **Kanban card:** `t_ba50565c`
- **Role:** Reviewer / read-only inspection
- **Final verdict:** **APPROVED WITH WARNINGS**

## Executive Summary

The substantive desktop-project-folder design has progressed beyond the session's final recap: it was formalized under the root OpenSpec project, implemented in Hermes Agent commit `8b17d62904`, synchronized into canonical specs, and archived by root commit `521c7d7b`. Independent focused tests pass: the Python project-tree suite passes 41/41 and the Desktop workspace-groups suite passes 82/82. Canonical OpenSpec specs validate 17/17.

The skills-governance work also landed in root commit `34851e9adf`, including the Project Activation Gate, Single Target Invariant, workflow/governance separation, and renamed governance directory. However, the session's claim that legacy `opsx-*` references were fully purged is not accurate: active workflow/governance material still contains legacy aliases and legacy workflow paths. The session recap is also stale relative to on-disk state and cites an exploration path that does not exist. The underlying exploration survives in the archived canonical change, so this is traceability/status drift rather than loss of the design.

## Findings Summary

| Severity | Count | Finding IDs |
|---|---:|---|
| CRITICAL | 0 | — |
| WARNING | 3 | W-01, W-02, W-03 |
| INFO | 2 | I-01, I-02 |

## Scope

### Session evidence reviewed

- Active messages for session `20261006_083443_c97909` from the local read-only `state.db`.
- User requirements concerning single-target OpenSpec storage, project activation, tool-first OpenSpec skills, workflow/governance separation, `opsx-*` cleanup, skills commit, and the Desktop assigned-folder/CWD defect.
- The session's final progress recap and proposed next steps.

### Specifications and design reviewed

- `O:/workspaces/openspec/changes/archive/fix-duplicate-project-workspaces/proposal.md`
- `O:/workspaces/openspec/changes/archive/fix-duplicate-project-workspaces/design.md`
- `O:/workspaces/openspec/changes/archive/fix-duplicate-project-workspaces/tasks.md`
- `O:/workspaces/openspec/changes/archive/fix-duplicate-project-workspaces/specs/desktop-project-discovery/spec.md`
- `O:/workspaces/openspec/changes/archive/fix-duplicate-project-workspaces/specs/project-subfolder-lanes/spec.md`
- `O:/workspaces/openspec/changes/archive/fix-duplicate-project-workspaces/explorations/2026-10-05-fix-duplicate-project-workspaces-exploration.md`
- `O:/workspaces/openspec/specs/desktop-project-discovery/spec.md`
- `O:/workspaces/openspec/specs/project-subfolder-lanes/spec.md`
- `O:/workspaces/oss/hermes-agent/apps/desktop/DESIGN.md`
- `O:/workspaces/oss/hermes-agent/apps/desktop/ENGINEERING.md`
- `O:/workspaces/oss/hermes-agent/website/docs/developer-guide/architecture.md`
- `O:/workspaces/ai_agents/collaboration/openspec/specs/code-review-mandate/spec.md`
- `O:/workspaces/rules/shared/lessons-digest.md`
- `O:/workspaces/rules/guides/templates/lessons-learned.md`

### Source and tests reviewed

- `O:/workspaces/oss/hermes-agent/tui_gateway/project_tree.py`
- `O:/workspaces/oss/hermes-agent/tests/tui_gateway/test_project_tree.py`
- `O:/workspaces/oss/hermes-agent/apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts`
- `O:/workspaces/oss/hermes-agent/apps/desktop/src/app/chat/sidebar/projects/workspace-groups.test.ts`
- Root skills/governance files changed by commit `34851e9adf`, including:
  - `.agents/skills/devops/hermes-desktop-workspace-management/SKILL.md`
  - `.agents/skills/openspec-workflow/SKILL.md`
  - `.agents/skills/openspec/*/SKILL.md`
  - `rules/skills/openspec-workflow-governance/SKILL.md`
  - `rules/skills/openspec-workflow-governance/references/openspec-agent-workflow-guide.md`
  - `rules/shared/rules-index.md`

### Commits inspected

- Root skills/governance commit: `34851e9adf1e8b90f681abb9e3366c8b99c6a866`
- Hermes Agent implementation commit: `8b17d6290406...`
- Root OpenSpec sync/archive commit: `521c7d7b02a6b434cbddc9d283f90d405b7673ce`

## Requirements-to-Progress Matrix

| Requirement inferred from the session | Current evidence | Status |
|---|---|---|
| Activate a single owning project before OpenSpec work and avoid duplicate artifacts | `.agents/skills/openspec-workflow/SKILL.md:41-46` and `rules/skills/openspec-workflow-governance/SKILL.md:22-29` define the mandatory Project Activation Gate and Single Target Invariant. The completed change exists only under root `O:/workspaces/openspec/`; the two formerly cited exact exploration locations are absent. | **Met**, with traceability caveat W-03 |
| Merge/update the portable workspace OpenSpec skill set and distinguish execution from governance | Commit `34851e9adf` adds/updates the workspace OpenSpec skills, adds `activate-project`, renames the governance directory, and retains separate execution/governance responsibilities. | **Met** |
| Prefer Hermes-native tools while retaining CLI portability | `activate-project/SKILL.md` explicitly selects Hermes-native filesystem tools; the updated skill set contains tool-first/fallback guidance. | **Met** |
| Remove stale `opsx-*` nomenclature from the named active skill/governance surfaces | Git evidence at commit `34851e9adf` and current files still contains legacy `/opsx-*` aliases and `opsx-*.md` workflow paths. | **Partially met** — W-02 |
| Preserve assigned project folders and avoid collapsing them into the parent repo | `project_tree.py:320+` seeds declared folders and synthesizes `::folder::` lanes; archived specs require explicit-project priority and folder lanes. | **Met** |
| Route fresh/live sessions to the owning explicit project before auto-project fallback | `workspace-groups.ts:432+` implements `liveSessionProjectId`; focused tests cover explicit folders, Windows/WSL normalization, repo-root-only sessions, and worktrees. | **Met** |
| Make project-folder sessions/New Chat use the intended folder CWD | The tree supplies non-git folder lanes whose path is the assigned folder; Desktop grouping preserves explicit-project ownership. The focused implementation tests pass. | **Met at unit/integration level**; live GUI was not exercised in this inspection |
| Complete the OpenSpec lifecycle, not merely an exploration draft | The change is archived under `openspec/changes/archive/fix-duplicate-project-workspaces`, tasks are checked, canonical specs exist, and strict spec validation passes 17/17. | **Met** |

## Findings

### W-01 — Session progress recap is stale and would direct duplicate work

- **Layer:** Coordination / SDLC state
- **Spec reference:** `code-review-mandate` Evaluation Basis; session requirement to provide accurate progress and next steps.
- **Evidence:** The final session recap says the skills changes are still awaiting commit and the Desktop problem is only at exploration, recommending that implementation begin next. On disk, root commit `34851e9adf` already contains the skills/governance work, Hermes Agent commit `8b17d62904` contains the implementation, and root commit `521c7d7b` synchronizes and archives the OpenSpec change.
- **Impact:** Following the recap literally would repeat already-completed work and may create conflicting edits or redundant change artifacts.
- **Suggested resolution:** Replace the next-step guidance with current state: no new implementation should start for this defect unless a live GUI regression reproduces. Any further work should begin with a fresh gap report against the archived requirements.

### W-02 — Claimed `opsx-*` purge is incomplete

- **Layer:** Skills governance
- **Requirement reference:** User request to review and fix stale `opsx-*` references across `.agents/skills/openspec-workflow`, `.agents/skills/openspec`, `rules/skills/openspec-workflow-governance`, and `rules/shared`.
- **Evidence:** Current files retain:
  - `.agents/skills/openspec-workflow/SKILL.md:227-228` — legacy `/opsx-*` aliases and `opsx-*.md` workflow examples.
  - `rules/skills/openspec-workflow-governance/SKILL.md:18` — legacy `/opsx-*` aliases.
  - `rules/skills/openspec-workflow-governance/SKILL.md:309-312` — `opsx-update-skill.md`, `opsx-explore.md`, `opsx-propose.md`, and `opsx-audit.md` paths.
  - The same legacy target paths remain in `references/openspec-agent-workflow-guide.md:797-800` at commit `34851e9adf`.
- **Impact:** The active governance corpus still teaches two naming systems, contradicting the session's “dọn sạch”/fully purged completion claim and risking agents loading obsolete workflow names.
- **Suggested resolution:** Open a separate scoped remediation change. Decide whether legacy aliases are intentionally supported; if yes, document them as compatibility aliases rather than claiming removal. If no, replace/remove all remaining references and verify with a repository-scoped `git grep` gate.

### W-03 — The exploration path cited by the session does not exist

- **Layer:** Documentation / traceability
- **Requirement reference:** Single Target Invariant and durable exploration artifact requirement.
- **Evidence:** Neither of these exact paths exists:
  - `O:/workspaces/oss/hermes-agent/openspec/workspace/explorations/2026-10-06-desktop-project-assigned-folders-and-new-session-cwd-exploration.md`
  - `O:/workspaces/openspec/workspace/explorations/2026-10-06-desktop-project-assigned-folders-and-new-session-cwd-exploration.md`
  The durable exploration is instead present at `O:/workspaces/openspec/changes/archive/fix-duplicate-project-workspaces/explorations/2026-10-05-fix-duplicate-project-workspaces-exploration.md`.
- **Impact:** The session's handoff sends future workers to a dead path. The design itself is not lost, but recovery requires repository search or knowledge of the archived change.
- **Suggested resolution:** Correct future handoffs to cite the archived canonical artifact. Do not recreate a duplicate at either old location.

### I-01 — Desktop design and implementation align with the archived requirements

- **Layer:** Specs / architecture / code
- **Evidence:** The archived design requires explicit-project absorption, non-git subfolder lanes, and client-side explicit-project attribution. `project_tree.py` implements folder-lane IDs and declared-folder seeding, while `workspace-groups.ts` implements live-session project ownership before auto fallback. The approach fits the Desktop architecture: backend data is normalized before presentation, while the client performs immediate live-session overlay classification.
- **Assessment:** No blocking architectural drift found in the scoped implementation.

### I-02 — Focused regression evidence is green

- **Layer:** Tests / validation
- **Evidence:**
  - `scripts/run_tests.sh tests/tui_gateway/test_project_tree.py`: **41 passed, 0 failed**, exit 0.
  - `npm --prefix apps/desktop test -- src/app/chat/sidebar/projects/workspace-groups.test.ts`: **82 passed**, exit 0.
  - `openspec validate --specs --strict` from `O:/workspaces`: **17 passed, 0 failed**, exit 0.
- **Assessment:** The focused contract is covered. The Node run emits a non-failing experimental localStorage warning.

## Architecture and Design Assessment

The implemented split is appropriate:

1. **Backend tree normalization** owns durable project/repository/folder topology. This is the correct layer for synthesizing explicit non-git folder lanes and preventing auto-project duplication.
2. **Desktop live overlay classification** owns sessions that appear before the next backend refresh. Resolving explicit project ownership before repo-root fallback prevents transient duplicate cards and preserves project color/membership semantics.
3. **Non-git lane metadata** (`isGit: false`, `isMain: false`) protects the UI from offering branch-switch actions on plain filesystem folders.
4. **OpenSpec ownership** is now rooted at `O:/workspaces/openspec`, matching the user-approved single-target rule for this cross-root project context. Reintroducing a duplicate in `oss/hermes-agent/openspec` would violate that decision.

No unnecessary coupling was identified in the scoped source. The remaining risk is operational rather than architectural: a real Desktop smoke test should confirm the exact `Inspire-Robotics` interaction and Files pane CWD on the installed runtime.

## Verification Evidence

```text
Python project-tree suite:
41 tests passed, 0 failed; exit 0

Desktop workspace-groups suite:
1 file passed; 82 tests passed; exit 0

Canonical OpenSpec specs:
17 passed, 0 failed; exit 0
```

The live projects database also contains the expected explicit project and assigned folder:

```text
project: p_6a6786b4 / Inspire-Robotics
primary_path: O:\workspaces\devops-support\inspire-robotics
project_folders: O:\workspaces\devops-support\inspire-robotics (is_primary=1)
```

## Not Reviewed

- The installed Hermes Desktop GUI was not opened or restarted; no live click-through of the Inspire-Robotics `+` action or Files pane was performed. The user reserves runtime restarts.
- No full Hermes Agent or full Desktop regression suite was run; the inspection used the two focused suites tied directly to this design.
- Unrelated modified/untracked files in `O:/workspaces` and the pre-existing modification to `openspec/workspace/sessions/agent_share.md` were not reviewed.
- No source code, skill file, configuration, database, service, or runtime installation was modified by this inspection.

## Final Verdict

**APPROVED WITH WARNINGS.**

The desktop project-folder design is implemented, specified, archived, and supported by passing focused tests. The user should not initiate the previously proposed implementation phase again. Before declaring the broader session fully clean, reconcile the stale handoff, decide and enforce the intended legacy `opsx-*` compatibility policy, and cite the canonical archived exploration path. A live Desktop smoke test is the only remaining functional acceptance step not covered by this read-only inspection.
