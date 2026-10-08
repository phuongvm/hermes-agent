# Independent Code Re-review v2 — Desktop Sidebar Collapsible Tools

- Task: `t_6e37e3aa`
- Change: `desktop-sidebar-collapsible-tools`
- Candidate HEAD: `0d4d58e777ac4b72f8c6274e8bab59511dcfa437` plus the uncommitted scoped working-tree diff
- Verdict: **REQUEST CHANGES / PROOF GATE FAILED**
- Review scope: Reviewer-v1 remediation, sidebar source/tests, active OpenSpec delta, authorized Desktop architecture/design guidance, and the six commands prescribed by the task

## Acceptance audit

| Criterion | Result | Evidence |
| --- | --- | --- |
| Reviewer-v1 signed-out fixture remediation | PASS | `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts:20-25,120-139` binds `$connection.baseUrl` to the signed-out URL and resets both connection and terminal-auth state during teardown. Standalone suite passed 9/9. |
| New Session remains pinned above daily navigation | PASS | `apps/desktop/src/app/chat/sidebar/index.tsx:1584-1596`; integration ordering assertions at `chat-sidebar.integration.test.tsx:191-223`. |
| Search, Pinned, Sessions/Projects precede TOOLS | PASS | `apps/desktop/src/app/chat/sidebar/index.tsx:1600-1948`; Sessions and Projects ordering coverage at `chat-sidebar.integration.test.tsx:191-253`. |
| TOOLS defaults collapsed and has accessible disclosure state | PASS | `navigation.tsx:181-205` uses local default-false state, `aria-expanded`, `aria-controls`, linked content id, and hidden content; unit coverage at `navigation.test.tsx:43-91`. |
| Utility routing and profile/session state are preserved | PASS | Existing route callbacks and split behavior remain in `navigation.tsx:48-178`; focused routing, project scope, session resume, and new-session tests at `chat-sidebar.integration.test.tsx:225-299` and `navigation.test.tsx:75-112` passed. |
| Standalone, focused, full-sidebar, typecheck, and diff-check gates | PASS | Fresh independent commands all exited 0; see Verification evidence. |
| Required directory-wide scoped ESLint gate | **FAIL** | `npx eslint src/app/chat/sidebar/ --max-warnings 0` exited 1 with 4 errors and 9 warnings. |

## Finding

### [P1] Mandatory scoped ESLint command exits 1

The task explicitly requires:

`npx eslint src/app/chat/sidebar/ --max-warnings 0`

Fresh independent execution from `apps/desktop` exited 1 with 13 findings (4 errors, 9 warnings):

- `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts:8` — named import ordering error.
- `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts:38,48,68` — three `no-restricted-syntax` errors for reactive-value/ref synchronization patterns.
- `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts:41,76` — two padding warnings.
- `apps/desktop/src/app/chat/sidebar/connection-switcher.test.tsx:244` — restricted-global warning.
- `apps/desktop/src/app/chat/sidebar/gateway-groups-drag.test.tsx:76,92,97` — three restricted-global warnings.
- `apps/desktop/src/app/chat/sidebar/profile-rail-fleet.test.tsx:257,374` — three restricted-global warnings.

Because `--max-warnings 0` makes both errors and warnings blocking, this command cannot support an APPROVED verdict. Several findings are outside the sidebar refactor diff, but the card defines the whole directory as the required lint proof scope; a reviewer cannot narrow or waive that contract after execution.

Required remediation:

1. Route the directory-wide lint remediation to an authorized coder; do not add suppressions merely to satisfy the gate.
2. Re-run the exact command from `apps/desktop` and obtain exit code 0.
3. Re-run the already-green standalone/focused/sidebar/typecheck/diff-check matrix to guard against remediation regressions.
4. Keep OpenSpec task 1.5 unchecked unless its separate full-desktop-suite command (`npm --prefix apps/desktop run test`) is also executed successfully; the v2 card requested a full-sidebar run, not a new full-desktop run.

## Verification evidence

| Command | Result |
| --- | --- |
| `npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run test -- src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts` | exit 0; 1 file, 9/9 tests passed; 1.88s |
| `npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run test -- src/app/chat/sidebar/navigation.test.tsx src/app/chat/sidebar/chat-sidebar.integration.test.tsx` | exit 0; 2 files, 26/26 tests passed; 33.41s |
| `npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run test -- src/app/chat/sidebar` | exit 0; 46 files, 371/371 tests passed; 27.69s |
| `npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run typecheck` | exit 0 across renderer, Electron, E2E, and builder-config checks |
| `npx eslint src/app/chat/sidebar/ --max-warnings 0` from `apps/desktop` | **exit 1; 4 errors, 9 warnings** |
| `git diff --check -- apps/desktop/src/app/chat/sidebar` | exit 0 |

Durable command logs are in `O:/workspaces/_config/agent4070/hermes/kanban/boards/skills-kb/workspaces/t_6e37e3aa/`.

## Scope and governance notes

- Static inspection found no additional blocking correctness defect in the scoped sidebar refactor.
- No implementation files, tests, lint configuration, or OpenSpec task checkboxes were modified during review.
- The repository still does not contain the mandated named `code-review-mandate/spec.md` or a project-specific `lessons-learned.md`. Review used the loaded `openspec-verifier` mandate, the available lessons template, active change artifacts, prior findings, and the formally authorized Desktop architecture sources.
- OpenSpec task 1.5 remains correctly unchecked because this re-review did not execute the separate full-desktop test suite and the required ESLint gate failed.
