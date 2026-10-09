# Reviewer Findings v1: crew-desktop-tab-tile

- Card: t_13246230 (@reviewer, openspec-verifier). Downstream: t_2e793722 (@leader)
- Date: 2026-10-09 14:59 +07
- Inspected HEADs: oss/hermes-agent `5809c10731c` plus an uncommitted working tree (6 desktop files, +107/-9); oss/crew `cb12b56` plus an uncommitted `desktop/plugin.js` (+1/-1, sha256 `a3f1ef8feb86bf97…`)
- Lens: round 1 (artifact). I read the diff cold, then ran every proof myself, then ran a regression baseline against clean HEAD and a mutation probe.
- Context loaded: `ai_agents/collaboration/openspec/specs/code-review-mandate/spec.md`, the change delta `specs/desktop-sidebar-navigation/spec.md`, proposal.md, design.md, tasks.md, `apps/desktop/AGENTS.md`, and the surrounding source (`route-tile.tsx`, `pane-mirror.ts`, `pane-shell/tree/store.ts` `revealTreePane`, `sidebar/navigation.tsx`, `contrib/wiring.tsx`).
- Not available: no `lessons-learned.md` exists under `oss/hermes-agent/docs/lessons/`. Only the template at `rules/guides/templates/lessons-learned.md` exists.

## 1. Verdict: APPROVED WITH WARNINGS

The card's Done-when is met. All 4 tasks.md proofs and `openspec validate --strict` exit 0. I found 0 CRITICAL, 2 WARNING and 2 INFO items. The two WARNINGs (W1 and W2) are already routed to running follow-up card t_b9da8694 (@coder). Do not commit or archive until that card lands.

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| WARNING | 2 |
| INFO | 2 |

## 2. Proof Gates (re-run independently)

| # | Command | Exit | Raw evidence |
|---|---|---|---|
| T1 | `npm --prefix oss/hermes-agent/apps/desktop run typecheck` | 0 | tsc ran on all 4 targets (`.`, electron, e2e, electron-builder.config.cjs) with no diagnostics |
| T2 | `node --check oss/crew/desktop/plugin.js && node -e "...includes('asTile: true')..."` | 0 | `PASS` |
| T3 | `npm --prefix oss/hermes-agent/apps/desktop test -- src/app/chat/sidebar/navigation.test.tsx` | 0 | `Test Files 1 passed (1)` / `Tests 7 passed (7)` |
| T4 | `C:\nvm4w\nodejs\openspec.cmd validate crew-desktop-tab-tile --strict` (cwd `oss/hermes-agent`) | 0 | `Change 'crew-desktop-tab-tile' is valid` |

The T4 proof depends on the working directory. Run from `O:/workspaces` it exits 1 with `Unknown item 'crew-desktop-tab-tile'`, so it must run with cwd `oss/hermes-agent`.

## 3. Regression Controls

| Control | Exit | Result |
|---|---|---|
| `npx vitest run src/app/chat src/store src/components/pane-shell/tree src/app/session` on the working tree | 1 | 502 files: 497 pass, 5 fail. 4798 tests: 4790 pass, 8 fail |
| The same 5 failing files on **clean HEAD 5809c10731c** (isolated detached worktree in the card scratch, node_modules junctioned, removed after the run) | 1 | **The same 8 tests and the same 2 suite errors fail** (`connections.test.ts` vi.mock hoist `$connection`, `updates.test.ts` `storage` TDZ, plus `gateway-profile-request` x2, `profile-cache` x4, `review` x2) |
| Conclusion | n/a | The 8 failures already exist on HEAD and are not caused by this change. No regression in navigation, pane-shell tree or route tiles |
| `git diff --check -- apps/desktop` | 0 | No whitespace errors |
| Scoped ESLint over the 6 changed desktop files | 1 | 1 error (W1). There is also 1 warning at `use-session-actions/index.ts:2970` (`exhaustive-deps`), which exists on the HEAD baseline as well |
| Runtime plugin copies | n/a | `_config/.../plugins/crew/desktop/plugin.js` and `_config/.../desktop-plugins/crew/plugin.js` are byte-identical to `oss/crew/desktop/plugin.js` (`cmp`), and each contains `asTile: true` once |
| Wiring | n/a | `contrib/wiring.tsx:1140` sets `onNavigate: selectSidebarItem`, so the production click path does reach the new branch |

## 4. Scope Audit

Every changed file maps to tasks.md Tasks 1 to 3. The changed desktop files are `routes.ts`, `types.ts`, `store/route-tiles.ts`, `chat/sidebar/index.tsx`, `session/hooks/use-session-actions/index.ts` and `chat/sidebar/navigation.test.tsx`; on the crew side the only change is `desktop/plugin.js`. I found no out-of-scope source edits. `openspec/workspace/sessions/agent_share.md` is the coordination dashboard.

## 5. Findings by SDLC Layer

### Specs
- [INFO] I1, `specs/desktop-sidebar-navigation/spec.md`. The scenarios use GIVEN/WHEN/THEN, which strict validation accepts. Scenario 2 ("without ... reloading the iframe") and Scenario 3 ("tabstrip remains visible") hold only by construction: the code skips `navigateToWorkspacePage` and `openRouteTile` is idempotent. No automated assertion covers either one (see W2).

### Architecture / Design
- [INFO] I2, `store/route-tiles.ts:48` and `chat/sidebar/navigation.tsx:166`. `openRouteTile` now always calls `revealTreePane`, which changes behaviour for the existing "Open in split" context-menu caller. design.md §3 does not record this. `selectSidebarItem` (`use-session-actions/index.ts:1019`) also calls `revealTreePane` a second time, which is redundant. Resolution: remove the duplicate call and add a note in design.md. This is already part of t_b9da8694 (F3).
- `RouteTile.dir` widened from `SplitDir` to `TileDock` (`route-tiles.ts:17,41`). This fits `pane-mirror.ts` `dir?: (tile) => TileDock`, which already supports `center`. Persisted tiles that use the older `SplitDir` values are a subset of `TileDock`, so they stay valid. No migration is needed.

### Code
- [WARNING] W1, `apps/desktop/src/app/session/hooks/use-session-actions/index.ts:147`. ESLint fails with `perfectionist/sort-imports`: Expected "@/store/route-tiles" to come before "@/store/session-unread". On HEAD this file has 0 errors. `apps/desktop` `lint` runs inside the `check:*` scripts that `.github/workflows/js-tests.yml` executes, so CI would go red. Resolution: move the import into sorted order (t_b9da8694 F1).

### Tests
- [WARNING] W2, `apps/desktop/src/app/chat/sidebar/navigation.test.tsx:47-59, 221-225`. The new asTile tests copy the `if (item.asTile && item.route) openRouteTile(...)` branch into the test's own `onNavigate`. They never exercise the production branch in `selectSidebarItem`.
  - **Mutation proof (new evidence):** I applied the developer diff in an isolated clean worktree and confirmed 7/7 passing. I then deleted the production `if (item.asTile && item.route) {...}` block from `selectSidebarItem` and re-ran `vitest run src/app/chat/sidebar/navigation.test.tsx`. The result was still `Tests 7 passed (7)`, exit 0. The suite cannot detect removal of the feature it claims to cover.
  - Resolution: add a `selectSidebarItem` case to `use-session-actions.test.tsx`, following the existing pattern at L6485 (`fronts the workspace pane ... #72602`). Assert that `$routeTiles` contains `{path:'/crew', dir:'center'}`, that `navigate` is not called, and that `revealTreePane` is called with `'route-tile:/crew'` (t_b9da8694 F2).

### DevOps
- No pipeline changes. The runtime plugin copies are in sync (section 3).

## 6. Scope Declaration

- Reviewed: the 6 desktop diffs, `oss/crew/desktop/plugin.js` and both runtime copies, all change artifacts (proposal, design, tasks, spec delta), the surrounding source listed in the header, and the CI workflow `js-tests.yml` for the lint gate.
- Not reviewed: interactive Electron UI behaviour. I did not launch the desktop app; tab rendering and focus were checked by code tracing only. I also did not review `agent_share.md` content beyond the Updates Log, or the duplicate exploration file `openspec/workspace/explorations/2026-10-09-crew-desktop-tab-tile.md` beyond confirming it is byte-identical to the relocated copy (the original was not removed after relocation; Leader housekeeping).

## 7. Done-when Checklist

- [x] T1 typecheck: exit 0
- [x] T2 plugin check: exit 0
- [x] T3 navigation.test.tsx 7/7: exit 0
- [x] T4 / card proof `openspec validate crew-desktop-tab-tile --strict`: exit 0
- [x] No regression vs HEAD in the desktop navigation, store, pane-shell or session suites (the 8 failures also occur on HEAD)
- [ ] W1/W2/I2 remediation is open on t_b9da8694 (@coder, running). It must land and be re-verified before commit or archive.

The reviewer did not edit any implementation code. This report and the agent_share log line are the reviewer's only writes. Nothing was committed.
