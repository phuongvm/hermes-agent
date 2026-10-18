# Independent review: multi-plane session lifecycle and memory topology audit

- **Review card:** `t_53cf5af7` (@reviewer, `Verify: agent`). **Audited card:** `t_deb2f855` (@coder).
- **Artifact under review:** `docs/audit/multi_plane_session_and_memory_topology_audit.md` (221 lines, mtime 2026-10-18 18:24 +07).
- **Repo state at review:** `O:/workspaces/oss/hermes-agent`, branch `main`, HEAD `1ed8f61a0915bae80b5dd9f29a93139daad7ddcb`. No checkout or branch switch was made. The reviewer edited no source, test or audit file. The only file the reviewer added to the repo is this one.
- **Reviewed:** 2026-10-18, about 18:30–18:55 +07, from AGENT-4070 by profile `reviewer`.

## VERDICT: **REJECTED**: 1 HIGH, 3 LOW, 4 INFO

Most of the audit checks out. Of the 60+ file:line citations, AST facts, runtime numbers and evidence pointers that I re-checked, nearly all are exact (see §3). Findings F-1, F-2, F-4 and F-5 reproduce independently.

The rejection comes from one factual error in the core Q1 evidence chain, and it is the claim the audit uses to prove that CLI and Desktop share the pin store:

> §4.1: *"The one production writer is `SessionDB.set_session_pinned`."*
> §7 matrix: *"§4.1 (5 production readers, **1 writer**)"*

**This is false.** There is a second production writer of `sessions.pinned` that does **not** go through `set_session_pinned`, and it behaves differently (R-1). The Q1 headline, *"same durable store"*, still holds because both writers update the same column. The supporting invariant, *one writer and therefore one pin semantics*, does not hold. An accuracy audit cannot be approved with a false invariant in its proof chain. The corrections needed are documentation only.

## 1. Findings

| ID | Severity | Location | Defect | Required correction |
|---|---|---|---|---|
| **R-1** | **HIGH** | audit §1 Q1 row, §4.1 last paragraph, §7 row 2 | **Missed second production writer of `pinned`, with different semantics.** The dashboard route `PATCH /api/sessions/{id}` (`hermes_cli/web_routers/sessions.py:927-934`, `operation='sidebar'`) goes to `_mutate_session_request` (`hermes_cli/web_server_sessions.py:253`), then `mutate_session` / `_mutate_offline_store`, then `hermes_state_runtime.mutate_runtime_session` (`:427`, `:479`), then `hermes_state_mutations.apply_action`. That function writes `pinned` with `db._set_lineage_column_in_transaction(conn, 'pinned', …)` (`hermes_state_mutations.py:104-107`; `METADATA_FIELDS` at `:5`). It **never calls `set_session_pinned`**, so it skips that method's *"pinning also clears `hidden`"* rule (`hermes_state_sessions.py:1026-1033`, ref #106171). The Desktop client sends pin toggles to that route shape: `setSessionPinnedRemote` → `mutateSessionHttp(id,'PATCH',{pinned})` (`apps/desktop/src/api/sessions.ts:427-428`, called from `store/session-pin-sync.ts:200`). The gateway's `api_server._handle_patch_session` (`gateway/platforms/api_server.py:3174-3177`) does call `set_session_pinned`. So the three surfaces use **two** write paths. **Reproduced behavioural difference** (isolated temp store, §2.4): CLI path → `pinned=1 hidden=0`, listed; sidebar-mutation path → `pinned=1 hidden=1`, **missing from** `list_sessions_rich(include_pinned=True)`. | Correct §4.1 and §7: list both production writers (`SessionDB.set_session_pinned` and `hermes_state_mutations.apply_action` via `mutate_runtime_session`) and state the `hidden`-clearing difference. Keep the Q1 headline ("same store: yes") but drop "all surfaces share one write path". Record the difference as a new finding with an owner. It may be intended for the canonical Bot Chat, which `set_session_pinned` also keeps hidden, but for an ordinary hidden row it repeats the #106171 symptom. Whether it is a product defect is a separate card, not part of this audit. |
| R-2 | LOW | audit §1 F-2 evidence cell | *"`.venv` pytest run: `5 failed, 24 passed`"* is cited as evidence for `tests/agent/test_memory_provider_caller_profile.py`. That count belongs to the **combined 5-file run** from §5.4. The file on its own gives **5 failed, 13 passed** (reviewer run, §2.2). | Name the combined invocation in F-2, or quote the single-file count. |
| R-3 | LOW | audit §3, test-pollution fence | `SessionDB` is given as *"230 production / 2936 tests/"*. Recomputed from the same `graph.json` (undirected degree, non-`tests/` neighbours): **234 / 2932**, sum 3166. 8 of the non-test neighbours have no `source_file` at all. `AIAgent` 200/1339 and `MemoryManager` 46/179 match exactly. | Correct the number, or state how source-less nodes were classified. |
| R-4 | LOW | audit §6 evidence row | *"The report header (line 3) reads 'Host: intel_nuc / DESKTOP-01'"* sits under the evidence directory `docs/evidence/intel_nuc_docker_storage_20261018/`. That header is in `O:/workspaces/docs/intel_nuc_docker_storage_report.md:3` and `…_safety_report.md:3`, which are outside the hashed evidence set. The fact is true, but the citation points at the wrong place. | Cite the report file path. Keep the hashed `get_vhd.txt:3` as the primary proof of identity. |
| I-1 | INFO | audit §6 reconciliation | The Honcho memory plane is **no longer independent** on this question. When asked during this review, `honcho_reasoning` now answers "DESKTOP-01 is the same machine as intel-nuc", **citing `t_deb2f855` itself**. A later audit that counted this as corroboration would be circular. The primary evidence (`get_vhd.txt:3` `user=DESKTOP-01\phuong`, collected over `ssh 192.168.100.110`, report `:203-206`) does support the reconciliation. | Advisory: future audits should treat post-audit Honcho recall as derived, not as primary evidence. |
| I-2 | INFO | audit §3 method | Rules `O:/workspaces/rules/AGENTS.md` §11 Tier 2 say *"Use ONLY `god_nodes`, `shortest_path`, `get_neighbors`"*. The audit used a custom BFS script over `graph.json` (`profiles/coder/cache/scratch/t_deb2f855_graph_probe.py`) instead of the Graphify tools. The operations are equivalent, and the reviewer's BFS reproduced all 7 paths, but this departs from the letter of the rule. The audit does disclose it. | Advisory only. |
| I-3 | INFO | audit F-3 | Not re-run by the reviewer. `scripts/run_tests.sh` re-provisions the environment, and a reviewer must not mutate it. The `curl: (23)` failure matches an earlier independent reviewer observation on this host (`agent_share.md` Updates Log, 2026-10-13 23:00 [REVIEWER]). Accepted as reported, not independently reproduced. | None. |
| I-4 | INFO | review process | Required context `code-review-mandate/spec.md` was **not found** under `O:/workspaces/rules` or `.agents`. A project `lessons-learned.md` does not exist. Only `rules/guides/templates/lessons-learned.md` (template) exists. There is no active OpenSpec change delta for this audit. The review used the card contract, root/module `AGENTS.md`, and `rules/AGENTS.md` §11 instead. | Governance gap; Commander awareness. |

## 2. Independent proof (raw, reviewer-run)

### 2.1 Card proof body and adjacent contracts (`.venv` Python 3.14.7, pytest 9.1.1)

```
$ .venv/Scripts/python.exe -m pytest tests/hermes_cli/test_sessions_pin.py -q -p no:cacheprovider
5 passed in 1.76s                     PROOF_BODY_RC=0
$ … tests/hermes_state/test_pinned_lineage.py tests/hermes_state/test_pinned_archived_sidebar.py \
    tests/hermes_cli/test_prune_spares_pinned.py tests/agent/test_memory_boundary_commit.py -q
11 passed in 3.25s                    ADJ_RC=0
```

### 2.2 F-2 reproduction

```
$ .venv/Scripts/python.exe -m pytest tests/agent/test_memory_provider_caller_profile.py -q -rf   -> rc=1
FAILED …::test_call_from_another_profiles_scope_is_refused
FAILED …::test_multiplexed_call_with_no_known_profile_is_refused
FAILED …::test_multiplexed_call_without_a_caller_scope_is_refused_even_for_the_process_profile
FAILED …::test_lifecycle_hooks_from_another_profiles_scope_are_refused
FAILED …::test_one_provider_instance_is_never_served_to_a_second_profile
5 failed, 13 passed in 3.16s
captured: "…refused (memory_caller_profile_unresolved): … caller profile home 'C:\\\\Users\\\\phuong\\\\…\\\\beta', provider bound to '…\\\\alpha'"
$ python -c "h=str(Path(r'C:\Users\x\.hermes\profiles\alpha')); print(h in ('bound to %r' % h))"   -> False
combined 5-file run: 5 failed, 24 passed in 19.11s
```

F-2's diagnosis is confirmed: the refusal codes are correct, and the only failure is `%r` escaping at `agent/memory_caller_scope.py:61,123,131`. A side detail the audit did not mention: line 123 logs the *owner* key in `normcase` (lower-case) form. The tests only assert `str(beta_home)`, so the audit's `%s` remediation still makes them pass.

### 2.3 Graph (Tier 2), from `graphify-out/graph.json`

- `built_at_commit` = `1ed8f61a…` = HEAD. 257,961 nodes, 624,153 links, `directed=False`. **Fresh. Matches.**
- God nodes top 10 (`.graphify_analysis.json` → `gods`): identical to audit §3, degree for degree.
- All 9 resolved node ids / locations match: `_cmd_pin` L841, `_cmd_pinned` L859, `cmd_sessions` L1280, `set_session_pinned` L1019, `SessionDB` L461, `MemoryManager` L222, `MemoryProvider` L84, `spawn_context_thread` L29, `AIAgent` L214.
- `_cmd_pin` edges: `contains`, `calls → _not_found`, `rationale_for`. Matches the audit's "no direct call edge" claim.
- Label collision: `.set_session_pinned()` resolves to the test double (`tests/…/test_sessions_pin.py` L35) and the production method (L1019). Matches.
- `_init_memory -[calls EXTRACTED]-> MemoryManager`. Matches.
- Reviewer BFS (production-only fence) reproduced all 7 shortest paths in audit §3 hop for hop, including the undirected `AIAgent → acp_adapter_session → hermes_constants → agent_init → MemoryManager` path.

### 2.4 R-1 behavioural probe (isolated temp `HERMES_HOME`, no real store touched)

Script: `profiles/reviewer/cache/scratch/t_53cf5af7_pin_paths.py`. Two hidden sessions; one pinned by `db.set_session_pinned`, the other by `apply_action(db, conn, id, 'sidebar', {'pinned': True})` after `validate_action` passed.

```
cli-row  pinned= 1 hidden= 0
desk-row pinned= 1 hidden= 1
pinned listing ids: ['cli-row']
rc=0
```

Static trace for R-1: `hermes_cli/web_routers/sessions.py:930-934` → `web_server_sessions.py:253,286` → `hermes_state_runtime.py:441,479` → `hermes_state_mutations.py:83,104-107`. `rg 'set_session_pinned'` over production code finds callers only in `hermes_cli/sessions_cmd.py:851` and `gateway/platforms/api_server.py:3177`, plus non-production `evals/`.

**Calibration:** I verified that the dashboard route is a production writer and that Desktop's `setSessionPinnedRemote` sends `PATCH /api/sessions/{id}` with mutation-identity fields that this route consumes. I did **not** confirm which backend Desktop reaches in every connection mode (dashboard versus gateway API server). Treat that part as HYPOTHESIS until a separate card traces it.

### 2.5 Source plane (Tier 3): line citations checked

All of these match HEAD exactly: `sessions_cmd.py` 841-856, 859-873, 1254, 1255-1258, 1265-1272, 1303, 1306-1307; `hermes_state_sessions.py` 862, 911-936, 1019-1034, 1323, 1417-1432; `hermes_state_maintenance.py` 94-107, 246-248, 305-316, 499; `hermes_state_gateway.py:358`; `hermes_state_common.py:469`; `subcommands/sessions.py:284-299`; `agent_init.py` 1261-1270, 1269, 1299, 1309, 1356; `memory_manager.py` 222, 268-271, 288-298, 308-315, 613-644; `memory_provider.py` 29-33, 44-49, 84-103; `cli_session_mixin.py:576-583`; `run_agent.py` 214, 871-890; Honcho `__init__.py` 220-229/225, 335, 860; `client.py` 30, 560-565. The 4 `include_pinned=True` production readers (`api_server.py:3034`, `web_routers/sessions.py:224`, `web_routers/profiles.py:499,555`) are confirmed.

### 2.6 F-1, F-4, F-5 and runtime plane

- **F-1:** `agent/AGENTS.md:127-128` and `cron/AGENTS.md:94` say `skip_memory=True`. `cron/scheduler.py:2522` has `skip_memory=False`, blamed to `b09ddf77460c`. `ef04d846e95` (2026-08-21) is an ancestor of HEAD and its diff flips `True→False`. Both AGENTS lines are blamed to `4441a2a28d21` (2026-09-04). `agent_context` is set at `agent_init.py:1269`, and Honcho skips at `__init__.py:225`. The `MemoryProvider.initialize` docstring (`memory_provider.py:125-126`) makes skipping the provider's responsibility. **Confirmed.**
- **F-4:** the install workspace is `2e1b652e`, origin `phuongvm/workspaces`. sha1 prefixes match the audit exactly (`3270838df5→d56c4d85b0`, `b17b76f06f→716bed3287`, `9633f7db5e→79a3128c64`, `c290d37ba7→90758bd293`, `plugins/memory/__init__.py` SAME). `_cmd_pin` is at L816 there. An AST source-segment comparison finds `_cmd_pin` and `_cmd_pinned` **IDENTICAL**. **Confirmed.**
- **F-5:** `plugins/memory/honcho` has 17 files on `origin/main`/HEAD and 0 on `upstream/main` (`e6f57427c19`). `plugin-catalog/honcho.yaml` exists, and so does `hermes-honcho-plugin.yaml`, which the audit does not mention. `_LEFT_CORE` is at `memory_provider_migration.py:36`. Bundled-first lookup is at `plugins/memory/__init__.py:147-154`. **Confirmed.**
- **Live `state.db` (coder, `mode=ro`):** 64 columns; `pinned`/`hidden`/`archived` present; no index on `pinned`; WAL mode. Counts are now `(461, 0, 58)` and size 580,333,568 B, against the audit's `(460, 0, 58)` / 564,486,144 B. That difference is live growth after the audit, not an inconsistency.
- **Config / memory plane:** `honcho.json` `baseUrl https://honcho-api.ptdev.vip`; 7 hosts, all workspace `hermes_agent4070`, `peerName agent4070-bot`, one `aiPeer` per profile, `per-directory`/`hybrid`/`async`/`enabled`. `memory.provider: honcho` and limits 2200/1375 in default, coder and reviewer. Evidence `SHA256SUMS`: 16 lines, 0 failures. `docker.txt:20-22` lists the honcho containers. `DB-PG-SIZE` gives `honcho 4.36` GB in both sentinel reports. `liveness_after.txt:8-10` shows them "Up 3 days". **Confirmed.**

## 3. Scope, Zero-Checkout and side-effect audit

- **Coder's diff:** one new untracked file, `docs/audit/…audit.md`. The other dirty files in the checkout (`gateway/host_attach.py`, `hermes_cli/write_approval_commands.py` at 13:15, `hermes_cli/kanban_parser.py` at 16:40, `agent_share.md` at 13:15) were all modified before the card was claimed (about 18:07) and are not attributable to `t_deb2f855`. Branch `main` and HEAD did not change. **Scope discipline: PASS.**
- **Reviewer side effect (disclosed):** the first run of the §2.4 probe imported `hermes_state` from a bare interpreter without `HERMES_DISABLE_LAZY_INSTALLS=1`. That started the Hermes source-update dependency sync (`hermes_cli/venv_sync.py:509`) **inside the throwaway temp `HERMES_HOME`** (about 1.2 GB uv cache), and it was stopped by the 120 s timeout. Afterwards: no repo file newer than 18:40, no `.update-incomplete`/`.lazy-refresh-incomplete` marker, `git status` unchanged, no leftover python/uv process, and the temp dirs were deleted. The rerun used `HERMES_DISABLE_LAZY_INSTALLS=1`, the same guard `tests/conftest.py:187` sets.

## 4. What must change for APPROVED

1. **R-1:** correct §4.1, §7 and the Q1 evidence wording to name both production writers and the `hidden`-clearing difference. Add the difference as a finding (e.g. F-6, Medium) with an owner card.
2. **R-2, R-3, R-4:** correct the citations and numbers.
3. Re-run the card proof. A re-review then only needs to check items 1-2 (Round-3 contract lens).

## 5. Reproduce

```bash
cd O:/workspaces/oss/hermes-agent
git rev-parse HEAD                                                       # 1ed8f61a0915bae80b5dd9f29a93139daad7ddcb
.venv/Scripts/python.exe -m pytest tests/hermes_cli/test_sessions_pin.py -q            # 5 passed
.venv/Scripts/python.exe -m pytest tests/agent/test_memory_provider_caller_profile.py -q -rf   # 5 failed, 13 passed
python O:/workspaces/_config/agent4070/hermes/profiles/reviewer/cache/scratch/t_53cf5af7_graph.py   # nodes, degrees, labels
python O:/workspaces/_config/agent4070/hermes/profiles/reviewer/cache/scratch/t_53cf5af7_bfs.py     # 7 shortest paths
HERMES_DISABLE_LAZY_INSTALLS=1 .venv/Scripts/python.exe \
  O:/workspaces/_config/agent4070/hermes/profiles/reviewer/cache/scratch/t_53cf5af7_pin_paths.py   # R-1 behavioural difference
rg -n 'set_session_pinned' --type py -g '!tests/**' -g '!.venv/**' -g '!.worktrees/**' .
sed -n '83,108p' hermes_state_mutations.py                                # second writer
```
