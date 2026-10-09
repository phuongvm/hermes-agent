# Reviewer Findings — Apply Re-review (Round 2, Execution lens)

Card: t_b2abcf95 | Reviewer: reviewer | Date: 2026-10-08 23:20 (+07)
Verdict: APPROVED (advisory caveats below, none blocking)

## Proofs re-executed by reviewer (not taken from handoff)

| Gate | Command (cwd) | Result |
|---|---|---|
| 1.1 | node proposal.md existence assert (O:/workspaces) | OK, exit 0 |
| 2.1 | node plugin.js `order: 55` + `/crew` check (O:/workspaces) | OK, exit 0 |
| 2.2 | node runtime plugin.js existence (O:/workspaces) | OK, exit 0 |
| 3.1 | `npm --prefix oss/hermes-agent/apps/desktop test -- src/app/chat/sidebar/navigation.test.tsx` (O:/workspaces) | 5/5 passed, exit 0 |
| 3.2 | `npm --prefix oss/hermes-agent/apps/desktop run typecheck` (O:/workspaces) | 4 tsc targets clean, exit 0 |
| 4.1 | node dashboard manifest `after:openspec` / `/crew` (O:/workspaces) | OK, exit 0 |
| 5.1 | `C:\nvm4w\nodejs\openspec.cmd validate integrate-crew-desktop-dashboard --strict` (oss/hermes-agent) | "Change 'integrate-crew-desktop-dashboard' is valid", exit 0 |
| Card verdict | venv python `crew_card.py verdict --card t_b2abcf95` | rc=0, PASS, profiles/reviewer/crew/verdicts/t_b2abcf95.jsonl |
| Regression control | `npx vitest run src/app/chat/sidebar src/contrib` (apps/desktop) | 54 files / 435 tests passed, exit 0 |

sha256 of the 3 plugin.js copies (oss/crew/desktop, plugins/crew/desktop, desktop-plugins/crew): all ec02748f...7d7b.

## Round-1 items — landed?

| R1 item | Status | Evidence |
|---|---|---|
| 1 [CRITICAL] fabricated test names | FIXED | verification-apply.md L101-119 names match `it(` titles at navigation.test.tsx L49, L80, L98, L125, L159 (doc cites L46/L77/L95/L124/L158, off by ~3 lines after the new import lines; names exact). |
| 2 [CRITICAL] undisclosed core edits | FIXED | `git diff --stat HEAD -- use-session-actions/index.ts store/route-tiles.ts` empty; no `crew` string remains in either file; `git status` shows only navigation.test.tsx + agent_share.md modified in tracked code. |
| 3 [WARNING] order sort unproven | FIXED | New test L125-156 registers crew (55) before openspec (50) via `createPluginContext` and asserts `registry.getArea(SIDEBAR_NAV_AREA)` returns `openspec:nav, crew:nav`; exercises real sort at contrib/registry.ts L67; disposers clean up. |
| 4 [WARNING] re-activation / split-pane scenarios | ADDRESSED (trace only) | Documented as generic `navigateToWorkspacePage` / `SplitSubmenu -> openRouteTile` path; no dedicated test. Accepted as non-blocking: behaviour is pre-existing core, not changed by this card. |
| 5 [WARNING] agent_share.md stale | FIXED (with nit) | Situation/Status now Phase 3 APPLY; t_09c5bcc1 DONE. |
| 6 [INFO] proof 5.1 cwd | FIXED | tasks.md annotates cwd per proof. |

## Advisory (non-blocking)

- A1 [Low] verification-apply.md L166-168 and L199-205: backslash sequences in `C:\nvm4w\nodejs` and `O:\workspaces\_config\agent4070\...\crew\verdicts\t_b2abcf95.jsonl` were interpreted as escapes when the file was written (literal newlines, BEL 0x07, VT 0x0B, TAB). Quoted commands/paths are therefore garbled in the Commander-facing artifact. The canonical commands in tasks.md are correct.
- A2 [Low] agent_share.md Updates Log entries are future-dated (23:25 / 23:15 written before 23:20 wall clock) and not in chronological order.
- A3 [Info] verification-apply.md test line references are ~3 lines stale (see item 1).
