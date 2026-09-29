## 1. Tasks

- [x] 1.1 Fetch origin+upstream; record refs, merge-base, counts; merge-tree conflict forecast (13 content conflicts)
- [x] 1.2 Deprecation audit: a5bd246865b compat removal, migration 49, manifest drift
- [x] 1.3 Create external worktree O:/workspaces/wt-hermes-sync-v5 from origin/main with .venv/node_modules junctions
- [x] 1.4 git merge --no-commit upstream/main; resolve 13 conflicts preserving custom code; drop PLUGIN-COMPAT blocks (merge 9b61ce776e)
- [x] 1.5 Scan in-tree + all 7 profile plugin dirs for imports of compat-removed names (0 hits)
- [x] 1.6 Verify INV-1..INV-7 and extra preserved surfaces by grep against merged tree
- [x] 1.7 ruff F821 diff: tui_gateway deltas come from one parent each (late-bound via method_ctx.bind_module, present on tui_gateway.server). tests/tui_gateway: 16 failures; 15 identical on origin/main baseline; the 16th (test_gateway_lifecycle_set_covers_desktop_card_tools) is an upstream defect: test + tool-render-class.ts byte-identical to upstream/main, where fc042f1d67b changed CARD_TOOL_NAMES to an `as const` array without updating the source-reading regex
- [x] 1.8 Targeted pytest: host_attach, buzz, teams, acp, coalescing, doctor (316 passed)
- [x] 1.9 Wide pytest: 59 failures, identical set on origin/main baseline worktree (0 merge regressions)
- [x] 1.10 Typecheck desktop (renderer+electron), ui-tui, i18n keys check; vitest gateway-reconnect (all exit 0)
- [x] 1.11 Desktop pack (Hermes.exe 204.0M) from worktree
- [x] 1.12 Shadow E2E on 9129: /api/status 200, /api/profiles 200
- [ ] 1.13 Six-command console-clean gate: doctor, security audit, dashboard, gateway, desktop pass on sanitized shadow; `hermes update --plan` not valid from worktree (shared .venv junction resolves the live checkout)
- [ ] 1.14 Commander approval; ff-only push to origin/main; verify ls-remote
- [ ] 1.15 Operator: external live ff, pre-warm, fleet config migration, restart; rerun 6 commands on production
