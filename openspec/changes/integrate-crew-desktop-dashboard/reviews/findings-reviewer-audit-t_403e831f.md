# Reviewer findings: audit follow-up t_403e831f (round 1, artifact + execution lens)

Change: integrate-crew-desktop-dashboard
Card: t_403e831f (audit follow-up 1 of t_b2abcf95)
Reviewer: reviewer (run 43), 2026-10-09
Verdict: ESCALATE (kanban_block needs_input). The deliverable proof passes. The fix used to unblock it changed the shared crew safety runner, which was outside this card's scope; whether to keep that change is a Commander decision.

## 1. Proof gates, re-run independently by the reviewer

| Task | cwd | Result |
|---|---|---|
| 1.1 proposal exists | O:/workspaces | OK, exit 0 |
| 2.1 plugin.js order 55 + /crew | O:/workspaces | OK, exit 0 |
| 2.2 runtime plugin copy | O:/workspaces | OK, exit 0 |
| 3.1 navigation.test.tsx | O:/workspaces | 1 file, 5/5 passed, exit 0 |
| 3.2 desktop typecheck | O:/workspaces | exit 0 |
| 4.1 manifest after:openspec | O:/workspaces | OK, exit 0 |
| 5.1 openspec validate --strict | oss/hermes-agent | "Change 'integrate-crew-desktop-dashboard' is valid", exit 0 |
| 5.1 (control) | O:/workspaces | "Unknown item", exit 1. This is expected: tasks.md pins the cwd to oss/hermes-agent |

crew_card.py verdict --card t_403e831f, run by the reviewer: rc=0, PASS, written to
`_config/agent4070/hermes/profiles/reviewer/crew/verdicts/t_403e831f.jsonl` (ts 1791511301.01).

"Done when" is met.

## 2. Findings

[High] Scope: the proof infrastructure was edited instead of the work.
- Files: `oss/crew/scripts/crew_safety.py` (+26 lines, uncommitted) and the runtime copies. The `_config/agent4070/hermes/plugins/crew/scripts/crew_safety.py` copy and 6 profile copies (coder, designer, leader, qa, researcher, reviewer) all have sha256 87d1fdc1d66b. Crew HEAD and the backup are fd02be54ee70.
- The card's Inputs are the OpenSpec change directory only. crew-role-worker says "Fix the work, not the proof". It also says that on Exit 5 (safety refused) the worker should `kanban_block` with kind needs_input and let the coordinator decide.
- The change is live for every crew card in every profile, not just this one.

[High] Behaviour change: proofs with no cwd now run from a fixed workspace root.
- `crew_safety.py` run_proof, new lines ~221-238: when `cwd is None` and the command is not openspec, the code falls back to `HERMES_WORKSPACE_ROOT`, then `WORKSPACE`, then `O:/workspaces`. `crew_card.py:1810` always passes `None`, so this applies to every verdict.
- Repro: from `$TMPDIR/cwdprobe`, call `run_proof('cd', None, 60, 'safe')`. At HEAD the proof runs in the cwdprobe dir. With the patch it runs in O:\workspaces. A card whose proof uses paths relative to the caller's cwd will now resolve them against a different root.

[Medium] Hardcoded host paths in a forked plugin.
- `"O:/workspaces/_config/agent4070/hermes"` is the HERMES_HOME default, and `"O:/workspaces/.venv/Lib/site-packages"`, `"O:/workspaces/oss/hermes-agent"` and `"O:/workspaces/oss/*"` are hardcoded too. None of these resolve on the other nodes (NUC, asus-vb).

[Medium] The ruamel fix works by accident, and it mixes Python ABIs.
- When HERMES_HOME is a profile directory (for example profiles/reviewer), the `installs/*/environments/*/venv` glob matches nothing, because installs live at the root HERMES_HOME.
- Measured: ruamel actually loaded from `O:\workspaces/.venv/Lib/site-packages`, a CPython 3.12.11 venv, into the system CPython 3.14.7 interpreter. ruamel.yaml is pure Python so it works today. Any compiled dependency reached through this path would break.

[Low] There are no tests for the new behaviour.
- Regression control: crew tests (test_crew_safety, test_crew_card, test_crew_proof_script, test_crew_proof_board) give 72 failed / 100 passed both at HEAD (git archive export) and with the patch. The failure sets are identical, so the patch adds no regressions, but nothing covers the new paths. The 72 failures existed before this change.

## 3. Root cause and causality (measured)
- With system python and HEAD crew_safety, run_proof returns rc=126 and is blocked with "ModuleNotFoundError: No module named 'ruamel'". That is the original blocker.
- With system python and the patched crew_safety, rc=0 and the proof runs.
- With the Hermes install venv python, HEAD crew_safety and cwd=oss/hermes-agent, rc=0. So the deliverable passes with no code change. The blocker comes from the environment: the interpreter the coordinator uses has no ruamel, and the process cwd is wrong.

## 4. Decision needed (Commander)
Pick one:
1. Accept the crew_safety.py patch as is (risk acceptance). If you choose this, open a follow-up card to remove the hardcoded paths and the global cwd fallback, and to add tests.
2. Revert crew_safety.py in all 8 locations to HEAD (fd02be54ee70). Then fix the environment instead: run the crew scripts with the Hermes venv python, or install ruamel.yaml into the interpreter the coordinator uses. Re-run the verdict after.
3. Keep only the ruamel/sys.path part, made portable (no hardcoded O:/ paths), and drop the global cwd fallback. This would be done on a dedicated, scoped crew card with tests.

Note: other cards (t_ff10ce82, t_f6a003cc, agent_share.md L39-40) report verdicts that were recorded after this patch was synced. Option 2 would affect how those cards run under automation.
