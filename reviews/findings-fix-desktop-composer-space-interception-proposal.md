# OpenSpec Proposal Audit — `fix-desktop-composer-space-interception`

## Verdict: REJECTED

The proposal identifies a real spacebar-interception class, but its requirements conflict with the Desktop keyboard-ownership contract and its design/tasks do not specify or prove the focus-routing and drag-sensor behavior promised by the delta spec. It must be revised before implementation begins.

## Scope

| SDLC layer | Reviewed sources |
|---|---|
| Proposal and OpenSpec artifacts | `openspec/changes/fix-desktop-composer-space-interception/{proposal.md,design.md,tasks.md,specs/desktop-composer-space-handling/spec.md,.openspec.yaml,README.md}` |
| Architecture and design | `AGENTS.md`; `apps/desktop/AGENTS.md`; `apps/desktop/src/AGENTS.md`; `apps/desktop/DESIGN.md`; `website/docs/developer-guide/architecture.md` |
| Composer implementation and tests | `apps/desktop/src/app/chat/composer/{composer-utils.ts,index.tsx,composer-utils.test.ts,hooks/use-composer-trigger.test.ts}` |
| Focus/keybinding implementation and tests | `apps/desktop/src/lib/keybinds/{composer-focus-keys.ts,composer-focus-keys.test.ts}` |
| Drag-sensor implementation and tests | `apps/desktop/src/app/chat/sidebar/{index.tsx,profile-switcher.tsx,reorderable-list.tsx,session-row.test.tsx}` |
| Governance | `ai_agents/collaboration/openspec/specs/code-review-mandate/spec.md`; `ai_agents/collaboration/docs/lessons/lessons-learned.md`; `openspec/workspace/sessions/agent_share.md` |

## Evidence

- Independent focused baseline command:
  ```text
  cd O:/workspaces/oss/hermes-agent/apps/desktop && npx vitest run src/app/chat/composer/composer-utils.test.ts src/app/chat/composer/hooks/use-composer-trigger.test.ts src/lib/keybinds/composer-focus-keys.test.ts
  ```
  Result: exit `0`; `3 passed`, `70 passed`, duration `33.26s`. This establishes only the existing behavior, not the proposed behavior.
- Attempted strict validation through `npx openspec validate fix-desktop-composer-space-interception --strict` from repository root could not start because npm returned `could not determine executable to run` (exit `1`). This is a local command-resolution limitation; it does not substitute for a successful validation result.
- The primary checkout is on `main`; `O:/workspaces/oss/hermes-agent`, `O:/workspaces/oss/crew`, `O:/workspaces/oss/hermes-nerve`, and `O:/workspaces` all report `main`. The hermes-agent worktree list shows feature branches only under `O:/workspaces/.worktrees/`.

## Findings summary

| Severity | Count |
|---|---:|
| CRITICAL | 3 |
| WARNING | 1 |
| INFO | 1 |

## Specs

### [CRITICAL] F-01 — Type-to-focus requirement conflicts with keyboard ownership and lacks a distinguishable activation rule

- **Spec reference:** `specs/desktop-composer-space-handling/spec.md:L14-L19` requires a space on a focused non-editing button/pill to focus the composer and insert a space. `design.md:L30-L31` is the only corresponding design direction.
- **Architecture conflict:** `apps/desktop/AGENTS.md:L128-L129` and `apps/desktop/DESIGN.md:L498-L509` require the focused surface to own its keys; `composer-focus-keys.ts:L188-L189` expressly preserves Space for focused activating controls. The existing regression test at `composer-focus-keys.test.ts:L169-L179` asserts that behavior.
- **Why this blocks:** A first Space on a focused native button is indistinguishable from keyboard activation. The requirement unconditionally repurposes it as text entry, breaking keyboard activation and contradicting the architecture without an explicit, bounded exception or an input signal that distinguishes intent.
- **Required resolution:** Reframe the requirement around a behavior that preserves focused-control activation (for example, only after focus lands on inert chrome, or with an explicit composer focus affordance), document the exception and its accessibility rationale in `design.md`, and replace the current contradictory test expectation with concrete acceptance tests for both button activation and composer routing.

### [CRITICAL] F-02 — Sensor-containment requirement has no design, task, or proof coverage

- **Spec reference:** `specs/desktop-composer-space-handling/spec.md:L21-L26` mandates that sortable/profile keyboard sensors not leak global Space capture. The proposal lists `profile-switcher.tsx` as an impact path (`proposal.md:L21-L25`).
- **Code evidence:** Keyboard sensors are created in `sidebar/index.tsx:L598-L601` and `profile-switcher.tsx:L394-L397`; profile squares spread `attributes` and `listeners` onto their focusable button in `profile-switcher.tsx:L1482-L1501`. Existing sidebar containment is specifically implemented and tested for session-row grab handles in `reorderable-list.tsx:L64-L72` and `session-row.test.tsx:L293-L355`, but there is no corresponding profile-square test.
- **Task gap:** `tasks.md:L1-L9` contains no sensor implementation task, no profile-switcher/sidebar test task, and no proof command for this required behavior.
- **Why this blocks:** The spec makes sensor containment a SHALL, yet the change has no executable plan to locate the listener leak, change it safely, or prove Space remains typeable while sortable/profile UI exists.
- **Required resolution:** Add a documented sensor-scoping design (including which element owns the keyboard activator and why), a discrete implementation task for every affected sensor path, and tests that prove (a) Space on the real drag handle still starts a keyboard drag and (b) Space in a composer/input is not prevented while profile and sidebar sortables are mounted.

### [CRITICAL] F-03 — Slash Space acceptance contract is internally incomplete and does not cover the explicit-selection behavior selected by the design

- **Spec reference:** `design.md:L27-L28` chooses Space acceptance only after `activeExplicit=true`; `spec.md:L6-L12` only specifies literal-space behavior and arrow selection followed by Enter or Tab. `tasks.md:L3-L4` does not require an explicit-arrow/Space regression case.
- **Code evidence:** Existing `acceptsTriggerCompletion` accepts Space whenever a non-empty slash query is not in free-text-argument stage (`composer-utils.ts:L202-L205`); the existing test asserts that current behavior at `composer-utils.test.ts:L78-L82`. `index.tsx:L817-L845` calls `preventDefault()` only after the acceptance predicate and item resolution succeed.
- **Why this blocks:** The selected algorithm changes an established input contract, but the normative scenarios omit the critical positive Space-after-arrow case and the tasks do not require tests for it. Implementers can satisfy the written scenarios while either disabling deliberate Space selection or accepting an unintended default completion.
- **Required resolution:** Add explicit normative scenarios and test tasks for all four boundaries: default-highlight Space inserts a literal delimiter; arrow-selected Space accepts exactly the chosen command if that behavior is intended; Tab and Enter retain their stated semantics; free-text slash arguments never get converted by Space. If Space-after-arrow is *not* intended, remove it from `design.md` and describe the final interaction consistently.

### [WARNING] F-04 — Task evidence does not demonstrate the claimed composer event path

- **Spec reference:** `tasks.md:L4-L8` requires `use-composer-trigger.test.ts` and `composer-focus-keys.test.ts` to pass, but each is phrased as a verification action rather than a task to add or alter tests.
- **Evidence:** `use-composer-trigger.test.ts:L163-L240` exercises trigger state/chip behavior directly; it does not dispatch a Space key through `ChatBar.handleEditorKeyDown` (`index.tsx:L664-L892`) or assert `defaultPrevented`. The current `composer-focus-keys.test.ts:L169-L179` encodes the behavior F-01 would reverse.
- **Required resolution:** Make test authoring explicit in the tasks and use either an integration test of the editor keydown path or a unit seam that verifies `preventDefault` is absent/present for each accepted and rejected Space path.

## Architecture and scope

### [INFO] F-05 — Scope needs a reproducible causal boundary before source changes are authorized

- **Evidence:** `proposal.md:L3` attributes the issue to three layers, but `design.md:L8` documents only the autocomplete reproduction. The sidebar already contains a documented containment remediation for session rows (`reorderable-list.tsx:L64-L72`), while the profile rail intentionally makes each square a keyboard-sortable button (`profile-switcher.tsx:L1443-L1501`).
- **Impact:** Without a minimal reproducible sequence for each claimed layer, the implementation could broaden from the proven autocomplete defect into unrelated drag/focus behavior.
- **Suggested resolution:** Record a deterministic reproduction and expected event ownership for autocomplete, focus routing, and each drag sensor separately. Scope implementation only to paths that reproduce, and retain tests for existing keyboard drag accessibility.

## Not reviewed

- No implementation diff exists for this proposal; no implementation correctness, full-desktop regression, packaging, or runtime GUI behavior was reviewed.
- No canonical `openspec` executable was available through `npx` in this environment, so strict validation was not completed.
- No source/test fixes were made by Reviewer.

## Approval conditions

Revise the proposal, design, delta spec, and tasks to resolve F-01 through F-03; add the test plan required by F-04; then run strict OpenSpec validation with an available canonical executable before requesting a new proposal audit.
