## Why

Merge upstream/main 16c59d0e78 (463 commits past merge-base 71cb7d60f8) into fork origin/main 6848756e74 (210 fork commits), absorbing the scheduled plugin-compat removal a5bd246865b, i18n language packs 9bcbe7b5df9 and config migration 49, while preserving fork invariants INV-1..INV-7 and custom surfaces: kanban OpenSpec enforcement, Teams pipeline (review gate, OAuth, rendered_html), Honcho own-peer reconciliation, host_attach self-probe deadlock guard, desktop signed-out reconnect guard.

## What Changes

- Merge commit `9b61ce776e` on `sync/upstream-main-v5` (parents `6848756e74`, `16c59d0e78`) in external worktree `O:/workspaces/wt-hermes-sync-v5`.
- 13 content conflicts resolved:
  - `gateway/host_attach.py`: keep fork self-probe deadlock guard (`c51c788780`); adopt upstream `_record_home` -> `record_home` rename.
  - `tests/gateway/test_host_attach_lifecycle.py`: keep both sides' tests.
  - `apps/desktop/src/store/gateway-reconnect.ts`: keep signed-out terminal rejection (INV-3); adopt upstream source-aware handler signature.
  - `plugins/platforms/teams/summary_writer.py`: adopt upstream i18n rendering; keep `rendered_html` template fast path.
  - `plugins/teams_pipeline/{cli,meetings,pipeline}.py`, `hermes_cli/{kanban,kanban_db,browser_connect}.py`, `hermes_state.py`, `plugins/memory/honcho/client.py`, `tools/terminal_tool.py`: keep fork code; drop PLUGIN-COMPAT blocks per upstream removal.

## Capabilities

### New Capabilities
- `sync-upstream-main-v5`: evidence-gated v5 reconciliation contract (isolation, invariants, compat-removal absorption, custom-surface preservation, release gate).

### Modified Capabilities
- None. Existing capability requirements are preserved, not weakened.

## Impact

- Code: 1500 files changed by the merge (upstream delta); 13 fork files hand-resolved.
- Dependencies: no npm/Python dependency version changes in fork workspaces except upstream `pyproject.toml`/`uv.lock` (`modal` bump); `apps/desktop` `builder` script drops `NODE_OPTIONS=--max-old-space-size=16384` (pack OOM risk to watch).
- Config: upstream migration 49 runs automatically on first load per profile (7 profiles).
- External plugins: AST scan of all 7 profile plugin dirs found 0 imports of compat-removed names.
