# Sync upstream main v5

## ADDED Requirements

### Requirement: Isolated reconciliation
The upstream merge SHALL be performed only in an external worktree and SHALL NOT modify the live checkout, production ports (9119, 8642), `state.db`, `kanban.db`, or the production Honcho workspace before Commander approval.

#### Scenario: Merge performed outside the live root
- **GIVEN** the live checkout `O:/workspaces/oss/hermes-agent` is clean at `origin/main`
- **WHEN** the v5 merge is performed
- **THEN** the merge commit exists only on branch `sync/upstream-main-v5` in `O:/workspaces/wt-hermes-sync-v5` and the live checkout HEAD is unchanged

### Requirement: Fork invariants preserved
The merged tree SHALL retain INV-1..INV-7: ACP `_prewarm_agent_runtime` and `HERMES_MAX_ITERATIONS`; Buzz `SUPPORTS_MESSAGE_EDITING = False` with WebSocket keepalive; desktop `reauthModalLatch` and `resolveHermesVersionLadder`; `@assistant-ui/tap` pinned to 0.9.8; `PYTHONUNBUFFERED=1` in the gateway launcher; coalesced profile reads with `_fallback_profile_dicts`; `self_repo_guard` and `MAX_EXPONENT = 15`.

#### Scenario: Invariant grep after merge
- **GIVEN** the merge commit
- **WHEN** each invariant symbol is searched in the merged tree
- **THEN** every symbol is present at the same location class as on `origin/main`

### Requirement: Compat-layer removal absorbed without breakage
Removal of the plugin compat layer (upstream `a5bd246865b`) SHALL NOT leave any in-tree module or any installed profile plugin importing a removed facade name.

#### Scenario: Import scan
- **GIVEN** the 2084 entries of the pre-removal `compat_manifest.json`
- **WHEN** in-tree Python and every `<HERMES_HOME>/plugins` and `<HERMES_HOME>/profiles/*/plugins` tree is AST-scanned for `from <facade> import <removed>` and `<facade>.<removed>`
- **THEN** zero hits are reported

### Requirement: Custom surfaces preserved through conflicts
Conflict resolution SHALL keep fork behavior in kanban OpenSpec enforcement, `hermes_state.remediate_qa_failure`, Honcho own-peer reconciliation, Teams pipeline (review gate, delegated OAuth, deliver, rendered_html fast path), `host_attach` self-probe deadlock guard, and the desktop signed-out reconnect guard, while adopting upstream renames and i18n.

#### Scenario: Targeted regression tests
- **GIVEN** the merge commit
- **WHEN** the host_attach, Buzz, Teams, ACP, coalescing, doctor, hermes_state, kanban, Honcho, terminal and browser test files run via `scripts/run_tests.sh`
- **THEN** zero tests fail

### Requirement: Release gate before publication
Publication to `origin/main` SHALL be fast-forward only and SHALL occur only after typecheck, desktop pack, shadow E2E on port 9129, and the six-command console gate pass with captured output.

#### Scenario: Fast-forward publication
- **GIVEN** all gates recorded as passed and Commander approval
- **WHEN** the branch is pushed
- **THEN** `origin/main` equals the merge commit and `6848756e74` is its first parent
