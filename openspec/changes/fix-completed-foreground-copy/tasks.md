# Tasks

## 1. Core Fix & TDD Regression Verification

- [ ] 1.1 Verify `_CompletedForeground` in `O:/workspaces/.worktrees/wt-fix-completed-foreground/tools/terminal_tool_result.py` implements `__copy__`, `__deepcopy__`, and `__reduce__` returning plain `str(self)` / `(str, (str(self),))` while leaving `confirms_foreground_completion()` and `model_tools.py` unchanged. `Proof: Set-Location O:/workspaces/.worktrees/wt-fix-completed-foreground; git diff -- tools/terminal_tool_result.py model_tools.py`
- [ ] 1.2 Verify TDD regression test `test_foreground_result_survives_copy_deepcopy_and_pickle_as_plain_str` in `tests/tools/test_terminal_completion_observer.py` passes across shallow copy, `copy.deepcopy(messages)`, and all pickle protocols (`0..pickle.HIGHEST_PROTOCOL`), asserting `type(...) is str`, exact text equality, and `confirms_foreground_completion(..., call) is False`. `Proof: Set-Location O:/workspaces/.worktrees/wt-fix-completed-foreground; O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe -E -m pytest -p no:cacheprovider -q tests/tools/test_terminal_completion_observer.py`
- [ ] 1.3 Verify compression worker snapshot isolation and related terminal observer tests remain green with zero regressions. `Proof: Set-Location O:/workspaces/.worktrees/wt-fix-completed-foreground; O:\workspaces\oss\hermes-agent\.venv\Scripts\python.exe -E -m pytest -p no:cacheprovider -q tests/tools/test_terminal_completion_observer.py tests/agent/test_compression_worker_isolation.py`

## 2. OpenSpec Validation & Independent Two-Axis Review

- [ ] 2.1 Validate the `fix-completed-foreground-copy` OpenSpec change strictly from the worktree root with zero errors or warnings. `Proof: Set-Location O:/workspaces/.worktrees/wt-fix-completed-foreground; C:\nvm4w\nodejs\openspec.cmd validate fix-completed-foreground-copy --strict --json`
- [ ] 2.2 Verify Zero-Checkout isolation (`§9 AGENTS.md`) confirming live primary `O:/workspaces/oss/hermes-agent` has zero branch mutation or untracked/unstaged diff from this change, and worktree diff is clean of whitespace/CRLF issues. `Proof: git -C O:/workspaces/oss/hermes-agent status --short --branch; git -C O:/workspaces/.worktrees/wt-fix-completed-foreground diff --check`
- [ ] 2.3 Run independent Two-Axis Code & Spec Review (Axis 1: Standards + Fowler smells; Axis 2: Spec fidelity against `specs/terminal-tool-result/spec.md` and `design.md` security invariant) via separate reviewer agent(s) and resolve any critical or warning findings before release. `Proof: Reviewer sign-off receipt recorded on swarm board / review log with both Standards and Spec verdicts = APPROVED`

## 3. Staged Fleet Rollout (Commander-Gated Live Cutover)

- [ ] 3.1 [Blocked by: 1.3, 2.1, 2.2, 2.3] With explicit Commander release authorization, commit verified changes in `O:/workspaces/.worktrees/wt-fix-completed-foreground` and push `HEAD:main` to `origin`, then cut over Wave 1 (`agent4070`). `Proof: git -C O:/workspaces/.worktrees/wt-fix-completed-foreground push origin HEAD:main && pwsh.exe -NoProfile -File "O:\workspaces\_config\agent4070\hermes\services\apply-live-hermes-cutover.ps1" -DeployAgent4070 -SkipDesktop`
- [ ] 3.2 [Blocked by: 3.1] Execute Wave 2 dry-run probe and staged rollout to affected Windows workstation `asus-vb` (respecting active `Hermes.exe` user guard unless Commander authorizes `-Force`), then confirm deployed commit SHA and zero new `_CompletedForeground` `TypeError` entries in `errors.log` / `tui_gateway_crash.log`. `Proof: pwsh.exe -NoProfile -File "O:\workspaces\.agents\skills\hermes-fleet-sync\scripts\fleet-sync.ps1" -Target asus-vb -DryRun && pwsh.exe -NoProfile -File "O:\workspaces\.agents\skills\hermes-fleet-sync\scripts\fleet-sync.ps1" -Target asus-vb && ssh asus-vb "powershell -NoProfile -Command 'git -C O:\workspaces\oss\hermes-agent rev-parse --short HEAD'"`
- [ ] 3.3 [Blocked by: 3.2] Execute Wave 3 dry-run preflight and staged rollout across all 5 Linux satellite nodes (`linux-all`: `intel_nuc`, `hp_15s`, `hermes-vm-01`, `pi120`, `pi183`) and confirm zero-trust health across every node. `Proof: pwsh.exe -NoProfile -File "O:\workspaces\_config\agent4070\hermes\services\apply-live-hermes-cutover.ps1" -Group linux-all -DryRun && pwsh.exe -NoProfile -File "O:\workspaces\_config\agent4070\hermes\services\apply-live-hermes-cutover.ps1" -Group linux-all`

## Workflow follow-up

- After staged fleet rollout and post-deploy verification pass with explicit Commander approval, sync delta specs (`openspec/specs/terminal-tool-result/spec.md`) and archive `fix-completed-foreground-copy`.
- Remove temporary worktree `O:/workspaces/.worktrees/wt-fix-completed-foreground` only after `origin/main` and live primary fast-forward verification succeed.
