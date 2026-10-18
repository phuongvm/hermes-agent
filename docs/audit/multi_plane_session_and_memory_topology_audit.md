# Multi-plane audit: session lifecycle (pin / keep flag) and memory topology

- **Card:** `t_deb2f855` (crew worker, `Verify: proof`). Downstream reviewer card: `t_53cf5af7`.
- **Repo:** `O:/workspaces/oss/hermes-agent`, branch `main`, HEAD `1ed8f61a0915bae80b5dd9f29a93139daad7ddcb` (2026-10-18 03:08 +07). No checkout, no branch switch, no production code edited. This file is the only file added.
- **Audited:** 2026-10-18, ~18:10–18:45 +07, from AGENT-4070 by profile `coder`.
- **Method:** the 4-tier routing in `O:/workspaces/rules/AGENTS.md` §11. Tier 1 is the module `AGENTS.md` files (concepts). Tier 2 is the Graphify AST (topology: `god_nodes`, `get_neighbors` and `shortest_path` only, with the test-pollution fence on). Tier 3 is ripgrep plus bounded `read_file` (implementation). Tier 4 is read-only runtime probes (SQLite `mode=ro`, pytest, config files). A fifth plane, cluster memory, compares Honcho (dialectic + search) against the local `MEMORY.md` and the on-disk evidence.

## 1. Verdict

| # | Question | Answer | Planes that agree |
|---|---|---|---|
| Q1 | Does `hermes sessions pin/unpin/pinned` write and read the **same durable store** as the Desktop sidebar? | **Yes.** All three go through `SessionDB` → `SessionSessionsMixin.set_session_pinned` / `list_sessions_rich(include_pinned=True)` → `sessions.pinned` in `state.db`. | AST, source, runtime, test |
| Q2 | Does a pin cover the whole conversation (compression lineage) and survive archive/prune sweeps? | **Yes.** The write goes through `_set_lineage_column` (a lineage CTE). Bulk prune, archive and auto-archive exclude pins through `_not_pinned_sql`. The listing back-fill ignores `archived`. | Source, tests |
| Q3 | How is memory laid out: what talks to what, and what guarantees a session-boundary flush? | `AIAgent` → `agent_init._init_memory` → `MemoryManager` (built-in + at most **one** external provider) → `MemoryProvider` ABC. `/new` queues `on_session_end` then `on_session_switch` as **one** FIFO task. Background work must use `spawn_context_thread`. | AGENTS.md, AST, source, tests |
| Q4 | Is the external memory provider here (Honcho) reachable, configured, and where does it run? | Configured in **every** profile checked (`memory.provider: honcho`), workspace `hermes_agent4070`, `baseUrl https://honcho-api.ptdev.vip`, one `aiPeer` per profile. The server stack (`honcho-api-1`, `honcho-deriver-1`, `honcho-mcp-1`, Postgres `honcho` DB 4.36 GB) runs on the Windows host **DESKTOP-01, also called intel_nuc, at 192.168.100.110**. | Config, on-disk evidence, Honcho memory, local memory (after reconciliation, §6) |
| Proof | `pytest tests/hermes_cli/test_sessions_pin.py` | **5 passed** (rc=0), in the crew proof environment and in the repo `.venv`. | Runtime |

**Findings that need an owner (none block this card's proof):**

| ID | Severity | Finding | Evidence |
|---|---|---|---|
| F-1 | Medium (doc drift) | `agent/AGENTS.md:127-128` and `cron/AGENTS.md:94` say *"Cron sessions pass `skip_memory=True` … memory providers intentionally do not run during cron."* The code has done the opposite since `ef04d846e95` (2026-08-21, *"cron agents now run with memory enabled like every other agent"*): `cron/scheduler.py:2522` passes `skip_memory=False`. The behaviour still holds **for Honcho only**, and only because the provider opts out itself: `_memory_provider_init_kwargs` sets `agent_context="cron"` (`agent/agent_init.py:1269`), and `plugins/memory/honcho/__init__.py:225` skips when `agent_context in {"cron","flush"}`. A provider that does not check `agent_context` **will** run during cron. Both `AGENTS.md` lines were written in `4441a2a28d2` (2026-09-04), after the code change, so the split copied stale text. | blame: `cron/scheduler.py:2522` → `b09ddf77460c`; `agent/AGENTS.md:127` → `4441a2a28d21` |
| F-2 | Medium (test defect, Windows only) | Five tests in `tests/agent/test_memory_provider_caller_profile.py` fail on Windows. `agent/memory_caller_scope.py:61,123,131` log profile homes with `%r`, which escapes Windows backslashes (`C:\\Users\\…`), while the test asserts `str(home) in logged` (`:153`, and similar at `:330`, `:445`). Repro: `str(Path(r'C:\Users\x\.hermes\profiles\alpha')) in ('bound to %r' % str(...))` evaluates to `False`. The refusal **logic** is correct: every failing assertion message already contains the right refusal code (`memory_caller_profile_unresolved` / `memory_provider_instance_shared`) and the right homes. Only the string match fails. | `.venv` pytest run: `5 failed, 24 passed`. Raw output is in §5.4. |
| F-3 | Low (process) | Root `AGENTS.md:143` says *"always `scripts/run_tests.sh`, never bare `pytest`"*, but the card's proof command is bare pytest. On this host `scripts/run_tests.sh` decided the environment was stale, tried to re-provision it, and failed to download pinned uv 0.12.3 (`curl: (23)`). The checkout was left unchanged (`git status` was identical before and after). So the bare-pytest proof is the only one that runs here today. | §5.3 |
| F-4 | Low (topology fact) | The running Hermes is **not** this checkout. The gateway and venv import from `…/installs/0324cf7278ede49b/environments/c4a6fa1e3f67465bb05868e53967a32e/workspace` (a `phuongvm/workspaces` clone at `2e1b652e`). Its `sessions_cmd.py`, `hermes_state_sessions.py`, `memory_manager.py` and `memory_provider.py` differ byte-for-byte from HEAD `1ed8f61a`. The `_cmd_pin` / `_cmd_pinned` bodies are **identical**, so Q1 also holds for the running install, but line numbers differ (`_cmd_pin` is at L816 there, L841 here). The crew proof imports the code under test from **this checkout** (`hermes_cli.sessions_cmd.__file__` = `O:\workspaces\oss\hermes-agent\hermes_cli\sessions_cmd.py`). | §5.2 |
| F-5 | Info (fork state) | `plugins/memory/honcho/` is **present** in this fork (17 tracked files on `origin/main`) and **absent** on `upstream/main` (`e6f57427`), which moved Honcho to `plugin-catalog/honcho.yaml` (`plugins/AGENTS.md:19-27`). `hermes_cli/memory_provider_migration.py:36` lists `honcho` in `_LEFT_CORE`. Discovery is bundled-first (`plugins/memory/__init__.py:147-154`), so the in-tree copy takes precedence over a catalog install with the same name. Expect a conflict on the next upstream sync. | `git ls-tree` on both refs |

## 2. Plane 1: module context (Tier 1, `AGENTS.md`)

Read in full: root `AGENTS.md` (169 lines), `agent/AGENTS.md` (152), `hermes_cli/AGENTS.md` (217) and `plugins/AGENTS.md` (162). The invariants that matter to this audit:

- **Facade + siblings** (root `AGENTS.md:101-117`): `hermes_state.py` is the `SessionDB` facade, and behaviour lives in `hermes_state_*.py`. The pin code is in `hermes_state_sessions.py`, not the facade. The AST confirms this (§3): `SessionDB -[uses]-> SessionSessionsMixin -[method]-> .set_session_pinned()`.
- **Prompt caching is sacred** (root `:20-23`, `agent/AGENTS.md:57-63`): never reload memories mid-conversation. This is why `MemoryManager.add_provider` snapshots its config gates once at registration (`agent/memory_manager.py:308-315`).
- **One process serves many profiles** (root `:128-132`; `agent/AGENTS.md:130-141`; `plugins/AGENTS.md:92-102`): lifecycle hooks fire under the owning profile's scope, and providers must start background work through `spawn_context_thread`. Enforced by `MemoryManager._owner_allows` → `caller_scope(...)` (`agent/memory_manager.py:268-271`).
- **Memory providers: bundled-first discovery, one external at a time** (`plugins/AGENTS.md:75`): enforced at `agent/memory_manager.py:288-298` (a second external provider is rejected with a warning).
- **Cron and memory** (`agent/AGENTS.md:127`, `cron/AGENTS.md:94`): **stale.** See F-1.

## 3. Plane 2: Graphify AST topology (Tier 2)

**JIT freshness gate:** `graphify-out/graph.json` `built_at_commit` = `1ed8f61a0915bae80b5dd9f29a93139daad7ddcb` = `git rev-parse HEAD`, so it **passes** and no rebuild was needed. The graph has 257,961 nodes and 624,153 links and is undirected. The probe script (read-only, BFS over `links`) excludes any node whose `source_file` is under `tests/` from paths and lists those nodes separately.

**God nodes** (from `.graphify_analysis.json`, top 10 by degree): PlatformConfig 3547, **SessionDB 3166**, Platform 2652, GatewayConfig 2070, GatewayRunner 1951, MessageEvent 1866, **AIAgent 1539**, get_hermes_home() 1468, SessionSource 961, MessageType 934. Both hubs in this audit's scope are in the top 7.

**Test-pollution fence in practice:** `SessionDB` has degree 3166, but only **230 production / 2936 tests/**. `AIAgent` has 200 / 1339. `MemoryManager` has 46 / 179. Without the fence, `tests/` nodes would dominate both hubs. The fence also caught a label collision: the label `.set_session_pinned()` resolves to **two** nodes, the production method (`hermes_state_sessions.py` L1019) and the test double `_FakeDB.set_session_pinned` (`tests/hermes_cli/test_sessions_pin.py` L35). Only the first was used.

**Resolved symbols** (all exactly 1 production node; graph location = source location verified in §4):

| Symbol | Node id | Graph loc |
|---|---|---|
| `_cmd_pin` | `hermes_cli_sessions_cmd_cmd_pin` | `hermes_cli/sessions_cmd.py` L841 |
| `_cmd_pinned` | `hermes_cli_sessions_cmd_cmd_pinned` | `hermes_cli/sessions_cmd.py` L859 |
| `cmd_sessions` | `hermes_cli_sessions_cmd_cmd_sessions` | `hermes_cli/sessions_cmd.py` L1280 |
| `set_session_pinned` | `hermes_state_sessions_sessionsessionsmixin_set_session_pinned` | `hermes_state_sessions.py` L1019 |
| `list_sessions_rich` | `…_list_sessions_rich` | `hermes_state_sessions.py` L1323 |
| `resolve_session_id` | `…_resolve_session_id` | `hermes_state_sessions.py` L862 |
| `SessionDB` | `hermes_state_sessiondb` | `hermes_state.py` L461 |
| `MemoryManager` | `agent_memory_manager_memorymanager` | `agent/memory_manager.py` L222 |
| `MemoryProvider` | `agent_memory_provider_memoryprovider` | `agent/memory_provider.py` L84 |
| `spawn_context_thread` | `agent_memory_provider_spawn_context_thread` | `agent/memory_provider.py` L29 |
| `commit_session_boundary_async` | `…_commit_session_boundary_async` | `agent/memory_manager.py` L613 |
| `AIAgent` | `run_agent_aiagent` | `run_agent.py` L214 |

**Shortest paths (production-only):**

```
_cmd_pin -> set_session_pinned (4 hops)
  _cmd_pin() -[contains]- sessions_cmd.py -[imports]-> SessionDB -[uses INFERRED]-> SessionSessionsMixin -[method]-> .set_session_pinned()
_cmd_pinned -> list_sessions_rich (4 hops)
  _cmd_pinned() -[indirect_call INFERRED]- sessions_cmd.py -[imports]-> SessionDB -[uses INFERRED]-> SessionSessionsMixin -[method]-> .list_sessions_rich()
cmd_sessions -> SessionDB (1 hop)          cmd_sessions() -[calls EXTRACTED]-> SessionDB
MemoryManager -> MemoryProvider (1 hop)    MemoryManager -[uses INFERRED]-> MemoryProvider
spawn_context_thread -> MemoryProvider (2) via agent/memory_manager.py imports
AIAgent -> MemoryManager (4 hops)          via acp_adapter/session.py -> hermes_constants.py -> agent/agent_init.py -[imports]-> MemoryManager
_cmd_pin -> MemoryManager (4 hops)         via sessions_cmd.py -> get_hermes_home() -> agent_init.py  (module-level imports only)
```

**What the AST cannot show (limits stated, not inferred):**

- `_cmd_pin` has **no** direct `calls` edge to `set_session_pinned` or `resolve_session_id`. The neighbours are only `contains`, `_not_found()` and the docstring. The call goes through the duck-typed `db` parameter (`db.set_session_pinned(...)`), which tree-sitter cannot resolve to a class. The path above goes through the module's `import SessionDB`, which is structural evidence and not a call trace. §4 confirms the real call line by line.
- `_cmd_pin` → `MemoryManager` is reachable only through module imports of `get_hermes_home`. There is **no** call-level coupling: `grep -n -i memory hermes_cli/sessions_cmd.py` shows no reference to the memory subsystem. **The session-pin path and the memory path are independent subsystems.** They share only `get_hermes_home()` (profile scoping) and the `state.db` home directory.
- The `AIAgent → MemoryManager` path goes through `acp_adapter` because the graph is undirected and unweighted. The real construction path is `AIAgent.__init__` → `agent_init.init_agent` → `_init_memory` → `MemoryManager()` (`agent/agent_init.py:1356`). The AST does show `_init_memory() -[calls]-> MemoryManager` at `agent/agent_init.py` L1299.

## 4. Plane 3: physical source (Tier 3)

### 4.1 Session pin lifecycle (one line per hop, all at HEAD `1ed8f61a`)

1. **Parser.** `hermes_cli/subcommands/sessions.py:284-299`: the `pin` and `unpin` subcommands take `session_ids` (nargs `+`); `pinned` takes `--json`.
2. **Dispatch.** `hermes_cli/sessions_cmd.py:1255-1258`: `_DB_HANDLERS` maps `"pin" → partial(_cmd_pin, pinning=True)`, `"unpin" → partial(_cmd_pin, pinning=False)` and `"pinned" → _cmd_pinned`. Line 1254: `_OBSERVATIONAL_DB_ACTIONS = {"list","stats","pinned"}`, so `pinned` opens the store **read-only**. Line 1303: `SessionDB(db_path=get_hermes_home()/"state.db", read_only=observational)`. Lines 1306-1307: a read-only open of a missing store prints the empty-state message instead of creating the file (`_print_empty_store`, `:1265-1272`).
3. **Pin write.** `sessions_cmd.py:841-856`: for each id, `resolve_session_id` (exact id, else a unique prefix, else `None`), then `set_session_pinned`, then print `Pinned session '<id>'.  (<title>)`. Any failure → `_not_found` → exit code 1. Successful ids are still written (partial success).
4. **Store write.** `hermes_state_sessions.py:1019-1034`: `_set_lineage_column("pinned", id, int(pinned))`. Pinning also clears `hidden` across the lineage, **except** the canonical Bot Chat (hidden + `CANONICAL_BOT_CHAT_TITLE`). `_set_lineage_column` (`:911-936`) runs a single `UPDATE … WHERE id IN (SELECT id FROM lineage) RETURNING id` inside `_execute_write`, so root and tip are updated atomically.
5. **Listing.** `sessions_cmd.py:859-873`: `list_sessions_rich(limit=1, include_pinned=True, exclude_sources=…)`, then a client-side filter `s.get("pinned")`. `hermes_state_sessions.py:1417-1432`: the pinned back-fill re-runs the filter with `include_archived=True` (a pin is an explicit keep, so an archived pin still lists) and still applies `hidden = 0` and the source filters.
6. **Retention.** `hermes_state_maintenance.py:94-107` defines `_not_pinned_sql` (spares pinned rows **and** unpinned continuations of a pinned segment). It is used by bulk prune/delete/archive unless `include_pinned` is set (`:246-248`), by `archive_stale_sessions(exclude_pinned=True)` (`:305-316`), and by the auto-prune of stale open sources (`:499`). `hermes_state_gateway.py:358` also excludes pinned rows from the gateway's empty-keyed-row sweep.
7. **Schema.** `hermes_state_common.py:469`: `pinned INTEGER NOT NULL DEFAULT 0`. No index covers `pinned`; the back-fill is a filtered scan.

**Other writers and readers of the same column** (ripgrep, production only): `gateway/platforms/api_server.py:3034`, `hermes_cli/web_routers/sessions.py:224` and `hermes_cli/web_routers/profiles.py:499,555` all call `list_sessions_rich(..., include_pinned=True)`. These are the Desktop/dashboard sidebar paths. The one production writer is `SessionDB.set_session_pinned`. This confirms the claim in the test docstring: CLI and Desktop share one store.

### 4.2 Memory lifecycle

- **Construction.** `agent/agent_init.py:1299-1374`, `_init_memory`. It returns early under `safe_worker_enabled()` (`:1309-1311`). A per-turn rebuilder can hand back an initialized manager (`:1345-1349`, #120116). Otherwise, when `memory.provider` is not a core sentinel (`agent/memory_provider.py:44-49`), it builds `MemoryManager()`, then `load_memory_provider(name)`, with a catalog recovery fallback (`recover_at_startup`), then `add_provider`, then `initialize_for_agent(**_memory_provider_init_kwargs)`.
- **Scope kwargs.** `agent/agent_init.py:1261-1270`: `hermes_home=str(get_hermes_home())`; `agent_context = platform if platform in ("cron","subagent") else "primary"`.
- **Manager invariants.** `agent/memory_manager.py:222-346`: built-in first, at most one external provider; schemas load **before** state changes (#9948); provider tools that shadow `_HERMES_CORE_TOOLS` are rejected (#40466); one serial background worker (`_sync_executor`); `_bound_scope` set by `initialize_all` (#140058).
- **Session boundary.** `memory_manager.py:613-644`, `commit_session_boundary_async`: `on_session_end(snapshot)` and then `on_session_switch(new_id, reset=True)` are submitted as **one** task to the single worker, so the end always runs before the switch, and `/new` is not blocked (#16454). It is called from `hermes_cli/cli_session_mixin.py:576-583` (CLI `/new`); with no history the switch runs inline.
- **Teardown.** `run_agent.py:871-883`, `shutdown_memory_provider` (idempotent flag, `on_session_end`, then `shutdown_all`, then the context engine). `run_agent.py:885-890`, `commit_memory_session` (rotation without teardown).
- **Provider contract.** `agent/memory_provider.py:84-225`: the multiplexing contract in the class docstring (`:87-103`) requires a NEW instance per load, all bindings taken from `initialize(hermes_home=…)`, and no module-level profile state. `spawn_context_thread` (`:29-33`) wraps `ctx_bound` (copies contextvars).
- **Honcho conformance** (in-tree copy, F-5): `plugins/memory/honcho/client.py:30` imports `agent.memory_provider.spawn_context_thread` and wraps it with owner registration (`:560-565`). `plugins/memory/honcho/__init__.py:335,860` start threads only through it. `:220-229` skips initialization for cron/flush contexts.

## 5. Plane 4: dynamic runtime (Tier 4)

### 5.1 Live `state.db` (read-only, `file:…?mode=ro`)

Profile `coder`, `O:\workspaces\_config\agent4070\hermes\profiles\coder\state.db` (564,486,144 bytes):

```
sessions has pinned/hidden/archived columns: True True True   (64 columns total)
count(*), sum(pinned), sum(hidden): (460, 0, 58)
indexes on sessions mentioning pinned: []
journal_mode: wal
```

The live schema matches `hermes_state_common.py:469`. This profile has **0 pinned** sessions, so there is nothing to compare at runtime between pin and listing on this store. The behaviour contract is covered by the tests in §5.4.

### 5.2 Which code actually runs

- `python` in the agent's PATH is the Hermes install venv (`…/c4a6fa1e3f67465bb05868e53967a32e/venv`), which **has no pytest**. The repo `venv` and `.venv` both have pytest 9.1.1 on Python 3.14.7.
- The crew proof runner (`plugins/crew/scripts/crew_safety.py:run_proof` → `proof_env()`) adds the repo venv `site-packages` and the Hermes source roots to `PYTHONPATH`. **Dry run under that exact environment** (not a verdict):

```
rc 0
.....                                                                    [100%]
5 passed in 3.42s
O:\workspaces\oss\hermes-agent\hermes_cli\sessions_cmd.py      <- code under test = this checkout
pytest 9.1.1
PYTHONPATH set: True
```

- Running install vs checkout (sha1 prefix, runtime → checkout): `sessions_cmd.py` 3270838df5 → d56c4d85b0 DIFF; `hermes_state_sessions.py` b17b76f06f → 716bed3287 DIFF; `memory_manager.py` 9633f7db5e → 79a3128c64 DIFF; `memory_provider.py` c290d37ba7 → 90758bd293 DIFF; `plugins/memory/__init__.py` SAME; `subcommands/sessions.py` DIFF. The `_cmd_pin` / `_cmd_pinned` bodies diffed between the two trees: **identical**. See F-4.

### 5.3 Canonical runner (`scripts/run_tests.sh`)

```
▶ environment missing or stale for /o/workspaces/oss/hermes-agent — re-running under run-in-hermes-env
→ Staging pinned uv 0.12.3 (win32-x64) into the pm store...
curl: (23) client returned ERROR on write of 16384 bytes
✗ failed to download pinned uv from https://github.com/astral-sh/uv/releases/download/0.12.3/... 
setup failed
```

`git status --short` and HEAD were the same before and after the failure. See F-3.

### 5.4 Adjacent behaviour-contract tests (`.venv`, bare pytest, read-only tmp homes)

```
tests/hermes_state/test_pinned_lineage.py
tests/hermes_state/test_pinned_archived_sidebar.py
tests/hermes_cli/test_prune_spares_pinned.py
tests/agent/test_memory_boundary_commit.py            -> all passed
tests/agent/test_memory_provider_caller_profile.py    -> 5 failed
  FAILED test_call_from_another_profiles_scope_is_refused
  FAILED test_multiplexed_call_with_no_known_profile_is_refused
  FAILED test_multiplexed_call_without_a_caller_scope_is_refused_even_for_the_process_profile
  FAILED test_lifecycle_hooks_from_another_profiles_scope_are_refused
  FAILED test_one_provider_instance_is_never_served_to_a_second_profile
5 failed, 24 passed in 7.30s
```

The first failure's assertion text shows that the refusal fired with the right code and homes. Only the `%r` escaping breaks the match:

```
E AssertionError: Memory tool pw_remember refused (memory_caller_profile_unresolved): the caller's profile is not
  the one its provider was initialized for; caller profile home 'C:\\Users\\phuong\\AppData\\Local\\Temp\\pytest-of-phuong\\
  pytest-573\\test_call_from_another_profile0\\.hermes\\profiles\\beta', provider bound to '...\\profiles\\alpha', multiplexed=False
tests\agent\test_memory_provider_caller_profile.py:153: AssertionError   (assert all(str(home) in logged for home in homes))
```

## 6. Plane 5: cluster memory (Honcho + local) and reconciliation

| Source | Claim about where Honcho runs |
|---|---|
| `honcho_reasoning` (dialectic, level low) | "Runs as a containerized service inside a Docker stack (WSL/Ubuntu) on `intel-nuc` (192.168.100.110) … PostgreSQL 4.36 GB." It also says: "**no records** … regarding `DESKTOP-01` hosting … Honcho." |
| Local `profiles/coder/memories/MEMORY.md:3` | "Honcho on **DESKTOP-01** via Cloudflare (honcho-api.ptdev.vip:8008, honcho-mcp.ptdev.vip:8787)." |
| `honcho_search` (two queries) / `honcho_profile` | No relevant excerpts; the peer card is empty in this fresh session. |
| On-disk evidence `O:/workspaces/docs/evidence/intel_nuc_docker_storage_20261018/` (card `t_2a97ee85`, hashes in its `SHA256SUMS`) | `docker.txt:20-22` lists `/honcho-api-1`, `/honcho-deriver-1` and `/honcho-mcp-1` (compose project `honcho`, `/home/ubuntu/workspaces/docker-compose/honcho`). The sentinel `DB-PG-SIZE` gives `honcho 4.36 GB`. `get_vhd.txt:3` reads `user=DESKTOP-01\phuong`. The report header (line 3) reads **"Host: intel_nuc / DESKTOP-01 (192.168.100.110)"**. |
| Config `O:/workspaces/_config/agent4070/hermes/honcho.json` (secrets redacted) | `baseUrl https://honcho-api.ptdev.vip`; hosts `hermes`, `hermes_researcher`, `hermes_leader`, `hermes_coder`, `hermes_reviewer`, `hermes_designer` and `hermes_qa`, all with workspace `hermes_agent4070`, `peerName agent4070-bot`, `sessionStrategy per-directory`, `recallMode hybrid`, `writeFrequency async`, `enabled true`. Each profile is its own `aiPeer`. |
| `config.yaml` (default, `coder`, `reviewer`) | `memory.provider: honcho`, `memory_char_limit 2200`, `user_char_limit 1375`. |

**Reconciliation:** the two memory planes **do not contradict each other**. They name the same machine. The on-disk evidence shows `intel_nuc` is the Windows host `DESKTOP-01` (Windows user `DESKTOP-01\phuong` on 192.168.100.110), with Honcho in its WSL Docker stack and published through Cloudflare at `honcho-api.ptdev.vip`. Each memory plane holds only half of the name mapping. The Honcho dialectic's statement that it has "no records" of DESKTOP-01 is a gap in what it knows, not a conflicting fact. If the audit had used only one memory plane, it would have reported a false contradiction or a wrong host.

**Not verified live (blocked):** I did not probe the Honcho endpoint over the network. `curl`/`ssh` commands match the user-defined deny rule `*curl*|*sh*` (`approvals.deny`), so the attempt was refused before it ran. Liveness evidence is limited to the `t_2a97ee85` capture (`liveness_after.txt`: honcho-api-1, -deriver-1 and -mcp-1 "Up 3 days" at 2026-10-18 ~16:29 +07) and to this session's working Honcho tool calls (`honcho_reasoning` returned a synthesized answer, so the API at `honcho-api.ptdev.vip` answered authenticated requests during this audit).

## 7. Cross-plane agreement matrix

| Claim | Concept (AGENTS.md) | Topology (AST) | Source | Runtime | Memory |
|---|---|---|---|---|---|
| Pin code lives in a sibling, not the facade | ✔ root `:101-117` | ✔ `SessionDB→SessionSessionsMixin→set_session_pinned` | ✔ `hermes_state_sessions.py:1019` | — | — |
| CLI and Desktop share one pin store | — | ◐ import-level only (duck-typed `db`) | ✔ §4.1 (5 production readers, 1 writer) | ✔ test 5/5; live schema has `pinned` | — |
| Pin covers the lineage; prune/archive spare it | — | — | ✔ `_set_lineage_column`, `_not_pinned_sql` | ✔ lineage/sidebar/prune tests pass | — |
| Session pin ⟂ memory subsystem | — | ✔ no call edge; only `get_hermes_home` | ✔ no memory reference in `sessions_cmd.py` | — | — |
| One external provider, serialized boundary flush | ✔ `plugins/AGENTS.md:75` | ✔ `MemoryManager→MemoryProvider`, `commit_session_boundary_async→_submit_background` | ✔ `memory_manager.py:288,613` | ✔ `test_memory_boundary_commit.py` passes | — |
| Caller-profile refusal | ✔ `agent/AGENTS.md:130-141` | ✔ `_owner_allows` edge | ✔ `memory_caller_scope.py` | ✘ 5 Windows test failures (F-2: log-format defect, logic proven) | — |
| Cron skips memory | ✘ says `skip_memory=True` | — | ✘ `skip_memory=False`; Honcho opts out itself | — | — |
| Honcho location | — | — | ✔ in-tree provider (F-5) | ◐ via `t_2a97ee85` evidence; live probe blocked | ✔ after reconciliation (§6) |

✔ agrees · ◐ partial or indirect · ✘ disagrees, reported as a finding · — not applicable

## 8. Recommended follow-ups (not done here: out of scope, needs an owner)

1. **F-1:** fix `agent/AGENTS.md:127-128` and `cron/AGENTS.md:94` to say that cron runs with memory enabled (`skip_memory=False` since `ef04d846e95`) and that providers receive `agent_context="cron"` and must skip writes themselves. *Docs only; a coder card in a worktree.*
2. **F-2:** make `agent/memory_caller_scope.py` log homes with `%s` (or make the test compare against `repr(str(home))`). Add `@pytest.mark.platforms` coverage for Windows. *A coder card in a worktree under `O:/workspaces/.worktrees/`; one invariant test, proven red on Windows first.*
3. **F-3:** decide whether crew proofs on this host may use bare pytest, or fix the pinned-uv download so `scripts/run_tests.sh` can provision itself.
4. **F-5:** before the next upstream sync, decide whether the in-tree `plugins/memory/honcho` stays (fork-local) or moves to the catalog plugin. It wins over a catalog install because discovery is bundled-first.

## 9. Reproduce

```bash
cd O:/workspaces/oss/hermes-agent
git rev-parse HEAD                                    # 1ed8f61a0915bae80b5dd9f29a93139daad7ddcb
python -c "import json;print(json.load(open('graphify-out/graph.json',encoding='utf-8'))['built_at_commit'])"
python O:/workspaces/_config/agent4070/hermes/profiles/coder/cache/scratch/t_deb2f855_graph_probe.py out.json   # Tier-2 probe
.venv/Scripts/python.exe -m pytest tests/hermes_cli/test_sessions_pin.py -q                                       # proof body
.venv/Scripts/python.exe -m pytest tests/agent/test_memory_provider_caller_profile.py -q                          # F-2 repro
```
