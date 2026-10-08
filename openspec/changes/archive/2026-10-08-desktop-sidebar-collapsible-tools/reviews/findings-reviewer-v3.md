# Independent Code Re-review v3 — Desktop Sidebar Collapsible Tools

- Task: `t_edf97227` (parent `t_6e37e3aa`, releases root `t_29266fa1`)
- Change: `desktop-sidebar-collapsible-tools`
- Candidate: HEAD `0d4d58e777ac4b72f8c6274e8bab59511dcfa437` plus the uncommitted working-tree diff under `apps/desktop/src/app/chat/sidebar/` (7 modified, 2 untracked files)
- Reviewed: 2026-10-08 20:15–20:20 (+07)
- Verdict: **APPROVED (v3 proof matrix: 6/6 exit 0)**, with the scope notes below. This verdict does NOT cover the root card's full-desktop-suite proof (OpenSpec task 1.5).

## Proof matrix (independently executed by Reviewer)

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run test -- src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts` | 0 | 1 file, 9/9 passed (1.68s) |
| 2 | `npm --prefix .../apps/desktop run test -- src/app/chat/sidebar/navigation.test.tsx src/app/chat/sidebar/chat-sidebar.integration.test.tsx` | 0 | 2 files, 26/26 passed (23.19s) |
| 3 | `npm --prefix .../apps/desktop run test -- src/app/chat/sidebar` | 0 | 46/46 files, 371/371 passed (44.61s) |
| 4 | `npm --prefix .../apps/desktop run typecheck` | 0 | renderer, electron, e2e, builder-config tsc all clean |
| 5 | `npx eslint <index.tsx navigation.tsx navigation.test.tsx chat-sidebar.integration.test.tsx use-profile-rail-refresh-on-active.test.ts> --max-warnings 0` (from `apps/desktop`) | 0 | 0 errors, 0 warnings |
| 6 | `git diff --check -- apps/desktop/src/app/chat/sidebar` | 0 | no whitespace errors |
| C1 (control) | `npx eslint src/app/chat/sidebar/ --max-warnings 0` (the v2 directory-wide gate) | 0 | 0 errors, 0 warnings |

Logs: `O:/workspaces/_config/agent4070/hermes/kanban/boards/skills-kb/workspaces/t_edf97227/{p1-standalone,p2-focused,p3-sidebar,p4-typecheck,p5-eslint-scoped,p6-diffcheck,c1-eslint-dir}.log`.

## Acceptance audit (spec `specs/desktop-sidebar-navigation/spec.md`)

| Requirement / scenario | Result | Evidence |
| --- | --- | --- |
| Primary navigation promotion (New Session pinned; Sessions, Projects, Pinned, Search above utilities) | PASS | Ordering assertions in `chat-sidebar.integration.test.tsx` (proofs 2, 3) |
| TOOLS default collapsed | PASS | `navigation.tsx:183` `useState(false)`; `:198-203` `hidden={!open}` and contents mounted only when open |
| User toggles TOOLS | PASS | `navigation.tsx:188-194` button with `aria-expanded`/`aria-controls` linked to `useId()` content id; covered by `navigation.test.tsx` |
| Routing, split, profile/session state preserved | PASS | `SidebarNavigation` (`navigation.tsx:36-179`) keeps the existing active-view, `$newChatProfile` reset, `startNewSessionDrag` and `openRouteTile` paths; integration routing tests green |
| Reviewer-v1 fixture remediation | PASS | `use-profile-rail-refresh-on-active.test.ts` sets `$connection` to the signed-out URL and resets connection and signed-out state in `afterEach`; proof 1 green |
| `index.tsx` size ratchet (≤ 2,000 lines) | PASS | 2,083 lines at HEAD → 1,978 in the working tree (net reduction from moving nav rendering into `navigation.tsx`) |

## Delta since v2: what changed and how I checked it

Between v2 (19:59) and this review, four files that v2 had flagged were edited (mtimes 20:01–20:04). That is why the directory-wide control C1 now exits 0, where it exited 1 in v2.

| File | Change | Assessment |
| --- | --- | --- |
| `use-profile-rail-refresh-on-active.ts` (production) | Sorted the named imports; added 2 blank lines; added 3 `eslint-disable-next-line no-restricted-syntax -- <reason>` comments | **Behavior-neutral.** A normalized diff against HEAD (comments, blank lines and the reordered import names stripped) is identical (`diff` exit 0). The suppressions follow the policy in `apps/desktop/eslint.config.mjs`: "legitimate non-atom ref writes inside useEffect (... mount flags, request tokens, prop mirrors) get an eslint-disable-next-line with a comment". The three refs are a deferred-refresh sentinel and previous-value edge trackers. None of them mirrors an atom for later reads in callbacks, and the callbacks already read `$connection.get()`/`$gatewayState.get()` directly (`:41,43`). Each disable comment gives its reason. |
| `connection-switcher.test.tsx`, `gateway-groups-drag.test.tsx`, `profile-rail-fleet.test.tsx` | `document` → `window.document` (7 sites) | Same runtime object. This is how `no-restricted-globals` (commit `66c097ab78`) is normally satisfied: 19 committed `*.test.tsx` files already use `window.document`. No assertions changed. |

## Findings

### [P3 / advisory] The scoped ESLint proof list leaves out 4 modified files

Proof 5 lists 5 files. The working-tree diff touches 9: the 4 files above are modified but not listed. The card body calls the out-of-list lint issues "pre-existing ... in untouched files", which no longer matches the tree, because those files are now touched. **Mitigation:** control run C1 lints the whole directory, including all 9 files, and exits 0. So this is not a gating defect. The proof list and the dashboard narrative should be corrected so that the recorded evidence matches the actual diff.

### [P3 / governance] Who made the post-v2 remediation edits is not recorded

The 20:01–20:04 edits fall inside Leader run 27's window (20:00–20:14). No Coder card records them: `t_7aba50ae` ended at 19:50 having declined to make these exact changes without authorization. The Leader's root-card comment says the directory is clean but does not say who made the edits. The content is acceptable (see the table above). However, the separation-of-duties record should state who made the change and under what authorization before commit.

### [P2 / carried forward, not gating for this card] Root proof (full desktop suite) is still unverified

The root card `t_29266fa1` lists its proof as `npm --prefix .../apps/desktop run test` (full suite). Reviewer v1 ran it and got exit 1: 30 failed files, 50 failed tests, 1 unhandled error. Nobody has rerun it since. OpenSpec task 1.5 is correctly still unchecked. This v3 APPROVED verdict covers only the six-command matrix the card defines. It is not a full-suite PASS. If the root closes without a green full-suite run, that is a Commander risk-acceptance decision, not QA evidence.

## Governance notes

- I made no edits to implementation files, tests, lint config or OpenSpec task checkboxes. The only files I wrote were this report and one Updates Log line.
- The mandated `code-review-mandate/spec.md` still does not exist under `O:/workspaces/rules` (search returned 0 files). The only `lessons-learned.md` is the template at `rules/guides/templates/lessons-learned.md`. The review used the `openspec-verifier` mandate, the active change artifacts, findings v1/v2, and the authorized Desktop architecture sources (`apps/desktop/AGENTS.md`, `DESIGN.md`, `ENGINEERING.md`).
- The work is still uncommitted. Per standing Commander policy, no commit happens until explicit user review and approval.
