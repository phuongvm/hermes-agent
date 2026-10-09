# Verification Report: crew-desktop-tab-tile

- Card: t_9a494213 (reviewer, crew worker pass, `Verify: independent`)
- Date: 2026-10-09 14:56 +07
- Inspected HEADs: oss/hermes-agent `5809c10731c` (uncommitted working tree), oss/crew `cb12b56` (uncommitted working tree)
- Lens: round 1 (artifact). I read the diff cold, then ran every proof myself.

## 1. Verdict

ALL PROOF GATES PASS (exit 0). The card's Done-when is satisfied.
There are 3 advisory findings (section 4). None of them breaks a tasks.md proof. F1 is a lint regression that the repo-wide check gate would flag.

## 2. Proof Gates (re-run independently by the reviewer)

| # | tasks.md proof | Exit | Raw evidence |
|---|---|---|---|
| T1 | `npm --prefix oss/hermes-agent/apps/desktop run typecheck` | 0 | Ran tsc on all 4 targets (`.`, `tsconfig.electron.json`, `tsconfig.e2e.json`, `electron-builder.config.cjs`). No diagnostics. |
| T2 | `node --check oss/crew/desktop/plugin.js && node -e "...includes('asTile: true')..."` | 0 | `PASS` |
| T3 | `npm --prefix oss/hermes-agent/apps/desktop test -- src/app/chat/sidebar/navigation.test.tsx` | 0 | `Test Files 1 passed (1)` / `Tests 7 passed (7)` |
| T4 | `C:\nvm4w\nodejs\openspec.cmd validate crew-desktop-tab-tile --strict` (cwd oss/hermes-agent) | 0 | `Change 'crew-desktop-tab-tile' is valid` |
| Card | `crew_card.py verdict --card t_9a494213` | 0 | `rc=0` / `verdict: PASS (fails on this card: 0)`, recorded in `profiles/reviewer/crew/verdicts/t_9a494213.jsonl` |

Extra regression controls I ran (not in tasks.md):

| Control | Exit | Result |
|---|---|---|
| `npx vitest run src/app/chat/sidebar src/store/route-tiles` (apps/desktop) | 0 | 46 files, 376/376 tests passed |
| `git diff --check -- apps/desktop` | 0 | No whitespace errors |
| Scoped ESLint over the 6 changed desktop files | **1** | 1 error, 1 warning (see F1) |
| ESLint on HEAD `use-session-actions/index.ts` via stdin (baseline) | 0 | 0 errors, 1 warning (the warning existed before this change) |
| sha256 of `oss/crew/desktop/plugin.js` and both runtime copies | n/a | All 3 are `a3f1ef8feb86bf97c376b66b41b696e84f9bcd028aba75eeb320bfcc9b12bd72` and carry `asTile: true` at line 231 |

## 3. Scope Audit (diff vs tasks.md)

Changed in `git -C oss/hermes-agent diff --stat -- apps/desktop` (6 files, +107/-9):
`routes.ts` (+2), `types.ts` (+1), `store/route-tiles.ts` (+9/-2), `chat/sidebar/index.tsx` (+2/-1),
`session/hooks/use-session-actions/index.ts` (+8), `chat/sidebar/navigation.test.tsx` (+93/-7).
oss/crew: `desktop/plugin.js` (+1/-1).

Every file maps to a tasks.md item (Tasks 1 to 3). I found no out-of-scope edits in the desktop or crew trees.
`openspec/workspace/sessions/agent_share.md` is also modified, but that is the coordination dashboard.

Spec delta: the three requirement scenarios trace to the code as follows.
- Scenario 1 (open as center tab): `use-session-actions/index.ts:1017-1022` calls `openRouteTile(route, 'center')`. `route-tile.tsx:103` passes `dir` to `paneMirror`, and `pane-mirror.ts:91` uses it as `pos`.
- Scenario 2 (re-focus, no duplicate): `openRouteTile` is idempotent (`route-tiles.ts:44`), and `revealTreePane` fronts the pane.
- Scenario 3 (tabstrip stays visible): this holds by construction because `navigateToWorkspacePage` is skipped, so `$workspaceIsPage` is not set. No automated test asserts it.

## 4. Findings

[Severity: Med] F1. `apps/desktop/src/app/session/hooks/use-session-actions/index.ts:147`: the new `import { openRouteTile } from '@/store/route-tiles'` is out of order. ESLint reports `perfectionist/sort-imports`: Expected "@/store/route-tiles" to come before "@/store/session-unread".
On HEAD this file has 0 ESLint errors, so the change introduces the error. `python scripts/check` / CI lint would fail.
Correction: move the import above `@/store/session-states`, or run `eslint --fix` on that file.
The CODER log entry (agent_share 15:25) says "ESLint 0 errors". That covered only `navigation.test.tsx`.

[Severity: Med] F2. `apps/desktop/src/app/chat/sidebar/navigation.test.tsx:47-59, 221-225`: the new asTile tests copy the `if (item.asTile && item.route) openRouteTile(...)` branch into the test's own `onNavigate` callback.
They never exercise the production branch in `selectSidebarItem` (`use-session-actions/index.ts:1017-1022`). If someone deleted the production branch, all 7 tests would still pass, so this is not a behaviour-contract test for the routing decision.
Spec scenarios 2 (reveal/focus) and 3 (tabstrip visible) also have no automated assertion.
Correction: add a case to `use-session-actions.test.tsx` that calls `selectSidebarItem({ ..., asTile: true, route: '/crew' })`. Assert that `$routeTiles` gains `{path:'/crew', dir:'center'}`, that `navigate` is not called, and that `revealTreePane` is invoked.

[Severity: Low] F3. Two related problems with `revealTreePane`:
- It is called twice. `openRouteTile` now always calls `revealTreePane('route-tile:'+path)` (`route-tiles.ts:48`), and `selectSidebarItem` calls it again right after (`use-session-actions/index.ts:1019`). The second call is redundant.
- Moving the reveal into `openRouteTile` also changes behaviour for the existing context-menu "Open in split" caller (`chat/sidebar/navigation.tsx:166`), which now reveals as well. This is plausibly desirable, but `design.md` §3 does not record it.
Correction: drop the duplicate call in `selectSidebarItem` and note the shared reveal in design.md.

Advisory (no action for the coder): the agent_share Updates Log carries timestamps (15:00 to 15:25) that are later than the wall clock at review time (14:56). The changes in oss/hermes-agent and oss/crew are uncommitted, and per Commander policy they stay that way until review and approval.

## 5. Done-when Checklist

- [x] tasks.md Task 1 proof: exit 0
- [x] tasks.md Task 2 proof: exit 0
- [x] tasks.md Task 3 proof: exit 0
- [x] tasks.md Task 4 / card proof `openspec validate crew-desktop-tab-tile --strict`: exit 0 (verdict PASS logged)
- [ ] F1/F2/F3 remediation: advisory, routed to a follow-up card. This does not block Done-when.

The reviewer did not edit any implementation code. This report is the reviewer's only write, apart from the agent_share log line.
