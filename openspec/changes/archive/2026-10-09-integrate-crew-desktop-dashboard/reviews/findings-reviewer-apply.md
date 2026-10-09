# Reviewer Findings — Apply Verification (t_b2abcf95, review round 1)

Change: integrate-crew-desktop-dashboard
Reviewer: reviewer (independent; reviewer did not edit any implementation file)
Date: 2026-10-08
Lens: Round 1 (Artifact) + execution of every proof gate
Verdict: REQUEST CHANGES (proof gates PASS; deliverable and scope defects block approval)

## 1. Proof gates — executed by the reviewer

| Gate | Command (cwd) | Exit | Output |
|---|---|---|---|
| 1.1 | node proposal.md exists check (O:/workspaces) | 0 | OK |
| 2.1 | node plugin.js order 55 + /crew check (O:/workspaces) | 0 | OK |
| 2.2 | node runtime copy exists check (O:/workspaces) | 0 | OK |
| 3.1 | npm --prefix oss/hermes-agent/apps/desktop test -- src/app/chat/sidebar/navigation.test.tsx | 0 | 1 file, 4/4 passed |
| 3.2 | npm --prefix oss/hermes-agent/apps/desktop run typecheck | 0 | 4 tsc targets clean |
| 4.1 | node manifest.json after:openspec + /crew check (O:/workspaces) | 0 | OK |
| 5.1 | C:/nvm4w/nodejs/openspec.cmd validate integrate-crew-desktop-dashboard --strict (oss/hermes-agent) | 0 | Change 'integrate-crew-desktop-dashboard' is valid |
| card | crew_card.py verdict --card t_b2abcf95 (Hermes venv python) | 0 | verdict PASS, file profiles/reviewer/crew/verdicts/t_b2abcf95.jsonl |

Extra: sha256 of oss/crew/desktop/plugin.js == plugins/crew/desktop/plugin.js == desktop-plugins/crew/plugin.js (ec02748f...).

## 2. Findings summary

| Severity | Count |
|---|---|
| CRITICAL | 2 |
| WARNING | 3 |
| INFO | 2 |

## 3. Findings by layer

### Tests / Evidence

[CRITICAL] F1 — reviews/verification-apply.md L89-93 lists four "Assertion Coverage" test names
("orders navigation items with contributed items sorted by order property", "collapses and expands TOOLS
section...", "routes to /crew when Crew navigation item is activated", "preserves active selection state
on toggle"). None exist. `grep -rF` over apps/desktop/src returns no match for any of them. The real tests
are navigation.test.tsx L46, L77, L95, L124. The audit report presents invented evidence.
Resolution: replace with the actual test names and what each asserts.

[WARNING] F2 — navigation.test.tsx L95-120 passes navItems already ordered [openspec, crew, settings] into
SidebarTools. It never exercises the `order` sort (contrib/registry.ts L67) or the scoped contribution path
(contrib/plugin.ts L284). Task 3.1 ("order 55 sorts below OpenSpec order 50") is therefore not proven by
its proof command; it passes regardless of the order values. Resolution: add a registry/contribution-level
test that registers order 55 and order 50 out of sequence and asserts the resolved order.

### Code / Scope

[CRITICAL] F3 — Undisclosed core desktop edits, uncommitted, owned by no card and absent from tasks.md:
- apps/desktop/src/app/session/hooks/use-session-actions/index.ts L53, L1052-1056: hardcodes
  `item.id === 'crew' || item.route === '/crew'` in core and routes it to openRouteTile instead of
  navigateToWorkspacePage.
- apps/desktop/src/store/route-tiles.ts L3, L48: adds revealTreePane on every openRouteTile.
mtimes 22:38-22:39, inside the t_517fae57 run window; no card's changed_files lists them.
Conflicts:
- design.md §3 says selectSidebarItem calls navigateToWorkspacePage; spec "First activation" requires the
  full-page Crew view. The edit opens a docked split tile instead.
- hermes-agent AGENTS.md rubric rejects plugins touching core files; a plugin id hardcoded in core is that.
- `item.id === 'crew'` is dead: contributed ids are scoped `crew:nav` (contrib/plugin.ts L284).
- No tests cover either change.
Resolution: revert both edits, or bring them into the change (spec + design + tasks + tests) through the
leader, with a generic mechanism rather than a plugin-specific branch. Disclose in verification-apply.md
whichever path is taken.

### Specs

[WARNING] F4 — spec scenarios "Re-activation of existing tab/tile" and "Split pane view" have no proof
gate in tasks.md and no test. Strict validation passing is structural only.

### Coordination / DevOps

[WARNING] F5 — agent_share.md still reports "Phase 2: PROPOSE", "Blockers: awaiting Commander approval
before implementation", and t_09c5bcc1 as GATED (it is done). Updates Log entries are stamped 23:05/23:15
while the run ended 22:58.

[INFO] F6 — Gate 5.1 depends on cwd: from O:/workspaces it exits 1 ("Unknown item"); from oss/hermes-agent
it exits 0. Other gates require O:/workspaces. The coder's first verdict entry (rc=1) is this failure.
tasks.md should state the cwd for each proof.

[INFO] F7 — `python crew_card.py verdict` with the system python 3.14 returns exit 5 (safety module needs
ruamel). Running it with the Hermes install venv python works.

## 4. Scope

Reviewed: tasks.md, proposal.md (grep), design.md §2-4, specs/desktop-sidebar-navigation/spec.md,
reviews/verification-apply.md, oss/crew/desktop/plugin.js, runtime copies (hash), oss/crew/dashboard/
manifest.json (proof), navigation.test.tsx, git diff of apps/desktop (3 files), contrib/registry.ts L40-75,
contrib/plugin.ts L284, sidebar/index.tsx L395-440, agent_share.md diff, crew_card.py cmd_verdict.

## 5. Not reviewed

- Live Desktop UI behaviour (plugin load, iframe /api/plugins/crew/board, theme sync): no running desktop
  in this headless session.
- oss/crew/dashboard/dist/index.js and plugin_api.py (staged in oss/crew, not part of tasks.md proofs).
- oss/crew/tests/test_crew_kanban_multi_board.py modification (outside this change).
- Web dashboard rendering (web/src/App.tsx partitionSidebarNav).
