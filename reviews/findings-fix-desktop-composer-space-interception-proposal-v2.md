# OpenSpec Proposal Re-review — `fix-desktop-composer-space-interception` v2

## Verdict: APPROVED

The v2 artifact set resolves all blocking findings F-01 through F-05 from `reviews/findings-fix-desktop-composer-space-interception-proposal.md`. It narrows the change to the reproducible slash command-name interception defect, defines the full acceptance boundary, specifies red-first and real-event-path coverage, and preserves keyboard ownership and existing sortable accessibility behavior without scope expansion.

## Scope

| SDLC layer | Reviewed sources |
|---|---|
| Change artifacts | `openspec/changes/fix-desktop-composer-space-interception/{proposal.md,design.md,tasks.md,README.md}` and `specs/desktop-composer-space-handling/spec.md` |
| Prior review | `reviews/findings-fix-desktop-composer-space-interception-proposal.md` (F-01–F-05) |
| Architecture | `AGENTS.md`; `apps/desktop/AGENTS.md`; `apps/desktop/DESIGN.md` |
| Composer source and tests | `apps/desktop/src/app/chat/composer/{composer-utils.ts,index.tsx,composer-utils.test.ts,paste-url-is-text.test.tsx,hooks/use-composer-trigger.ts}` |
| Focus and sortable guards | `apps/desktop/src/lib/keybinds/{composer-focus-keys.ts,composer-focus-keys.test.ts}`; `apps/desktop/src/app/chat/sidebar/session-row.test.tsx` |
| Governance | `O:/workspaces/ai_agents/collaboration/openspec/specs/code-review-mandate/spec.md`; `O:/workspaces/ai_agents/collaboration/docs/lessons/lessons-learned.md`; `openspec/workspace/sessions/agent_share.md` |

## Evidence

- `cd O:/workspaces/oss/hermes-agent && openspec validate fix-desktop-composer-space-interception --strict` exited `0` and printed: `Change 'fix-desktop-composer-space-interception' is valid`.
- One independent substantive regression check was run:

  ```text
  cd O:/workspaces/oss/hermes-agent/apps/desktop && npx vitest run src/app/chat/composer/composer-utils.test.ts src/app/chat/composer/hooks/use-composer-trigger.test.ts src/lib/keybinds/composer-focus-keys.test.ts src/app/chat/sidebar/session-row.test.tsx
  ```

  Raw result: `Test Files 4 passed (4)`; `Tests 84 passed (84)`; `REGRESSION_MATRIX_RC=0`.
- Zero-Checkout gate: `O:/workspaces/oss/hermes-agent`, `O:/workspaces/oss/crew`, `O:/workspaces/oss/hermes-nerve`, and `O:/workspaces` each reported branch `main`. `git worktree list` showed all non-main branches under `O:/workspaces/.worktrees/`.

## Prior findings reconciliation

| Finding | Resolution verified | Evidence |
|---|---|---|
| F-01 — focused-control Space conflict | Resolved by withdrawing type-to-focus Space delegation. The delta now requires the focused activating control to retain Space, matching the Desktop keyboard-ownership contract. | Delta spec “Focused controls keep keyboard ownership of Space” at `spec.md:L71-L80`; design D3 at `design.md:L82-L84`; existing implementation returns false for a Space event on an activating target at `composer-focus-keys.ts:L188-L189`; guard assertion passes at `composer-focus-keys.test.ts:L169-L179`. |
| F-02 — unplanned sensor containment | Resolved by withdrawing the sensor SHALL rather than silently leaving it unimplemented. The design records session-row containment as an unchanged, tested guard and profile-rail target-bound activation as a non-reproducing, separately scoped concern. | Causal boundary `design.md:L15-L22`; non-goal at `design.md:L32-L35`; guard task at `tasks.md:L36`; session-row Space behavior at `session-row.test.tsx:L337-L355`, included in the 84-test check. |
| F-03 — incomplete Space acceptance contract | Resolved. The delta specifies default-highlight, partial, inline, hover, arrow+Space, arrow+Enter, Tab, and query-change-after-arrow scenarios. The design's D1 truth table makes the command-name/argument-stage distinction and positive arrow behavior normative. | `spec.md:L3-L45`; `design.md:L39-L74`; implementation seam confirms the current acceptance predicate at `composer-utils.ts:L187-L205` and the actual `preventDefault()` gate at `index.tsx:L817-L845`. |
| F-04 — no real keydown-path proof | Resolved at plan level. Tasks require a new real-`ChatBar` keydown integration test, using the sibling provider harness and asserting both `fireEvent.keyDown` return/default prevention and resulting chip/plain text. They also require red-first failure conditions before the implementation task. | `tasks.md:L7-L25`; design D5 at `design.md:L90-L100`; reusable real-ChatBar harness at `paste-url-is-text.test.tsx:L49-L101`. |
| F-05 — missing causal boundary | Resolved. The design separates Layers A–D, marks only slash autocomplete as reproducible/in scope, and gives a source/test rationale for the retained focus and session-row paths. Tasks restrict implementation to the Layer A predicate and list the no-edit regression guards. | `design.md:L11-L35`; `tasks.md:L29-L37`; proposal scope at `proposal.md:L34-L45`. |

## Scope discipline

The proposal limits future production edits to `composer-utils.ts`; the only planned test modifications are `composer-utils.test.ts` and new `slash-space-accept.test.tsx`. It expressly excludes `index.tsx`, `use-composer-trigger.ts`, focus routing, and all sidebar/profile sensor source. That matches the design's stated causal boundary and avoids altering focused-control keyboard behavior merely to address the composer defect.

The proposed acceptance logic is coherent with the current event pipeline: `use-composer-trigger.ts:L142-L145` and `L188-L194` clear explicit selection after a query change; arrow navigation marks it explicit at `L249-L253`; `index.tsx:L839-L845` prevents default only after an accepted key resolves an item. The planned predicate therefore preserves literal Space without editing the keydown handler.

## Findings summary

| Severity | Count |
|---|---:|
| CRITICAL | 0 |
| WARNING | 0 |
| INFO | 1 |

### [INFO] Implementation evidence is intentionally pending

- **Spec reference:** `tasks.md:L27-L42`.
- **Evidence:** This card audits a proposal revision; the primary checkout contains no implementation diff for the proposed source/test files. The tasks correctly leave all implementation and execution checklist boxes unchecked.
- **Disposition:** This is expected at proposal stage. A later implementation review must execute tasks 2.1, 2.2, and 3.1–3.4 in the dedicated worktree before approval of code changes.

## Not Reviewed

- No implementation of this change, because none is proposed as completed and no dedicated implementation worktree/diff exists yet.
- No full Desktop suite, packaging, live GUI, or deployment behavior; those are not proposal acceptance evidence.
- No profile-rail runtime reproduction; v2 explicitly documents it as out of scope after source-level causal analysis and preserves it for a separately justified change if a reproduction appears.

## Approval conditions satisfied

1. F-01 through F-05 are resolved in the source-of-truth proposal, design, delta spec, and task plan.
2. Strict OpenSpec validation exited `0`.
3. The required focused-control and session-row guard behaviors pass independently.
4. No implementation source edits, commits, service restarts, or archive action were performed by Reviewer.
