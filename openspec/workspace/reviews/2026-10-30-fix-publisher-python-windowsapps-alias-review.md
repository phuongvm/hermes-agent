# Independent Review — Publisher Python Resolution Against WindowsApps Alias

- **Review card:** `t_dc70a257`
- **Reviewed handoff:** `t_ee092570`
- **Verdict:** **APPROVED**
- **Contract source:** `openspec/workspace/explorations/2026-10-30-fix-publisher-python-windowsapps-alias-exploration.md` §§3–5
- **Implementation:** `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`
- **Tests:** `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`

## Scope and Zero-Checkout

Zero-Checkout preflight passed: `O:/workspaces`, `O:/workspaces/oss/crew`, `O:/workspaces/oss/hermes-agent`, and `O:/workspaces/oss/hermes-nerve` each reported `main`. The Hermes branch worktree list contains only paths under `O:/workspaces/.worktrees/`.

Protected-scope check:

```text
$ git -C O:/workspaces diff --exit-code -- oss/hermes-agent/apps/desktop _config/agent4070/hermes/services/hermes-env.cmd _config/agent4070/hermes/services/synology_release_publisher.py
PROTECTED_DIFF_RC=0
```

Only the intended installer script is modified and the new service test file is untracked; none of the three prohibited paths has a tracked diff.

## Independent Execution Evidence

### Seam A + Seam B suite

```text
$ O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest -q O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py
190 passed in 136.44s (0:02:16)
```

Exit code: `0`.

### PowerShell AST parse

```text
$ pwsh -NoProfile -NonInteractive -Command '<Parser::ParseFile probe>'
PS_PARSE_ERRORS=0
```

Exit code: `0`.

## Spec Fidelity — PASS

The exploration contract is implemented at the public PowerShell process seam:

- **Gate:** `Test-WorkingPython` requires a leaf, rejects `Microsoft\\WindowsApps`, and runs a Python >= 3.10 live probe (`build-desktop-installer.ps1:142–153`), satisfying exploration §3, lines 74–78.
- **Ordered ladder:** explicit `HERMES_PYTHON`/`PYTHON` (`:174–177`), checkout virtualenv and newest PM Python through `Find-ManagedPython` (`:155–172`), then every ambient candidate through `Get-Command python -All` (`:180–182`), with fail-closed actionable error (`:183–186`). This matches §3, lines 80–93.
- **Toolchain consistency:** the PM-tools fallback populates missing Python environment variables from a working managed interpreter (`:238–245`), matching §3, lines 97–98.
- **Seam B coverage:** all five mandated behavioral cases are present at `test_synology_release_publisher.py:908–964`; the first is parameterized for both default build and `-PushOnly`. Assertions observe process return code, FakeDSM upload, Store-stub non-use, and no-network failure—not source text.
- **Scope:** protected-path diff check is clean, satisfying §5, lines 124–128.

## Standards / Design — PASS

`Resolve-PublisherPython` is the single public resolver seam, while `Test-WorkingPython` encapsulates validation/probing. The test suite invokes the real `pwsh.exe` process and a fake DSM rather than mocking private resolver internals; expected outcomes derive from the written contract. The two-axis review found no blocking documented-standard breach or Fowler smell. `python.cmd` support is a justified Windows process-fixture accommodation and is also required by the exploration’s stated shim option.

## One Additional Independent Check

The distinct scope-boundary command below independently verifies that the three explicitly prohibited paths have no diff:

```text
$ git -C O:/workspaces diff --exit-code -- oss/hermes-agent/apps/desktop _config/agent4070/hermes/services/hermes-env.cmd _config/agent4070/hermes/services/synology_release_publisher.py
PROTECTED_DIFF_RC=0
```

This passes the Completeness/Coherence lens for the stipulated out-of-scope boundary. The full process suite and AST parse above provide Correctness evidence.

## Conclusion

**APPROVED.** All specified resolver rungs, live validation, failure behavior, Seam A/Seam B process coverage, AST validity, and protected scope requirements are independently verified. No implementation edits, secret reads, DSM publication, service restart, checkout, commit, or archive action were performed by the reviewer.
