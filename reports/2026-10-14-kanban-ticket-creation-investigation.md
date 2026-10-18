# Kanban Ticket Creation Failure: Root Cause Analysis and Permanent Solution

- Card: t_8600e905 (crew worker, Verify: proof)
- Date: 2026-10-14
- Author: @coder
- Scope: `kanban_create` (Hermes core) and `propose_kanban_card` (DeskRPG plugin) failures seen across all agent4070 profiles
- Mode: read-only root cause audit. No production code was changed and no branch was checked out. All probes ran against local checkouts and the local `state.db` files.

---

## 1. Executive summary

The recurring `{"error": "title is required"}` from `kanban_create` is not a Hermes bug. It comes from the
9Router gateway (`oss/9router`, :20128, which Headroom :8203 sits in front of). When it translates
OpenAI-style tool schemas to Gemini, it deletes any object key named `title` at every depth. That includes
the property name `title` inside `properties`. The model never sees a `title` parameter, so it can't send one,
and the Hermes handler correctly rejects the call.

The `propose_kanban_card` failure comes from a second defect in the same 9Router function. A free-form
`{"type": "object"}` schema node gets a made-up required `reason` property. This applies to the Hermes
`tool_call` bridge's `arguments` field. The model obeys the changed schema and puts `reason` into the
arguments. The DeskRPG schema has `additionalProperties: false`, so Hermes rejects the call.

The permanent solution is to fix the 9Router schema cleaner so it treats `properties` as a map of names, not
schema keywords, and stops injecting placeholders into nested free-form objects. Section 6 adds a Hermes-side
contract gate and better error messages so the next proxy regression is caught before it reaches production.

---

## 2. Observed failure inventory (from session stores)

Source: every `state.db` under `O:/workspaces/_config/agent4070/hermes/` (default plus the profiles coder,
designer, leader, qa, researcher and reviewer). I read the `role='tool'` rows for `kanban_create` and
`tool_call`, then joined each one to the assistant `tool_calls` that produced it, so the arguments the model
actually sent could be checked.

| Class | Count | Tool | Error text | Defect? |
|---|---|---|---|---|
| A | 51 | `kanban_create` | `title is required` | Yes, proxy schema stripping (this report) |
| B | 2 | `tool_call` -> `propose_kanban_card` | `Additional properties are not allowed ('reason' was unexpected)` | Yes, proxy placeholder injection (this report) |
| C | 15 | `kanban_create` | `crew card refused: crew cards open only from the owner's /crew command` | No. The crew plugin guard works as designed (`plugins/crew/__init__.py:1057`, `:1077`) |
| D | 1 | `kanban_create` | `crew card refused: contract incomplete, missing: role` | No. Crew contract validation works as designed |

Class A details (computed, not estimated):

- Range: first failure 2026-08-14, last 2026-10-14 (this card's own intake: session `20261014_110435_5c9f7f`, message 311766).
- In 51 of 51 failed calls, the `title` key is absent from the arguments the model sent. In 0 calls was `title` present but empty. So the handler did not mis-parse anything; the model never produced the key.
- Spread: every profile that creates cards. default 17, leader 23 (14 + 9), designer 9, coder 1, reviewer 1. The models were `cb-gemini-flash-high`, `cb-agent-leader`, `cb-agent-designer`, `cb-agent-coder` and `cb-agent-reviewer`.
- Recovery: in 14 of 51 cases the next `kanban_create` in the same session succeeded. In 37 of 51 the next attempt failed again, the agent gave up, or it fell back to the `hermes kanban create` CLI.
- On the same models, 227 `kanban_create` calls succeeded with `title` present. The failure is intermittent per call, not per model. This fits combo routing in 9Router, where only some combo members go through the Gemini translator. That is an inference: I could not inspect live combo membership, see section 8.

Class B details: both calls (sessions `20260926_093639_2a690c` and `20260926_225407_ed21e9`) sent
`propose_kanban_card` arguments `{acceptance, body, reason, summary, title}`. The `reason` key is not in the
DeskRPG schema and appears nowhere in Hermes. It matches the exact placeholder 9Router injects (section 3.3).

---

## 3. Root cause analysis

### 3.1 Request path

```
Hermes agent (any profile)
  -> custom provider "headroom-proxy"  http://192.168.100.110:8203/v1   (config.yaml model.base_url)
     -> 9Router "ai-router"             http://192.168.100.110:20128/v1  (combo models cb-*)
        -> translator openai -> gemini  (open-sse/translator/request/openai-to-gemini.js)
           -> cleanJSONSchemaForAntigravity(fn.parameters)   <-- defect site
              -> Gemini / Antigravity / Gemini-CLI upstream
```

Evidence for the hops:

- `O:/workspaces/_config/agent4070/hermes/config.yaml`: `model.provider: headroom-proxy` and `base_url: http://192.168.100.110:8203/v1`. The fallback is `ai-router` at `:20128`.
- The failing sessions record `billing_provider=headroom-proxy` and `billing_base_url=http://192.168.100.110:8203/v1`.
- The fleet exploration doc `devops-support/inspire-robotics/openspec/workspace/explorations/2026-10-14-ir-hermes-fleet-and-portal-exploration.md:105` says: "custom:ai-router :20128 (fronted by Headroom proxy :8203)".
- 9Router `GET /v1/models` returns the `cb-*` ids with `owned_by: "combo"` (live probe, HTTP 200).

### 3.2 Defect 1: the property name `title` is deleted (causes class A)

File: `O:/workspaces/oss/9router/open-sse/translator/formats/gemini.js` (HEAD `8fcafe9b`, package 0.5.99)

- `:7-43`: `UNSUPPORTED_SCHEMA_CONSTRAINTS` contains `"title"` (line 32, added by `d3f61aac` on 2026-06-14). It also contains `default`, `format`, `examples`, `const`, `optional`, `if`/`then`/`else`, `gap`, `padding`, `fontSize`, and others.
- `:147-168`: `removeUnsupportedKeywords(obj, keywords)` walks every object and deletes any key found in that list:

  ```js
  for (const key of Object.keys(obj)) {
    if (keywords.includes(key) || key.startsWith("x-")) { delete obj[key]; continue; }
    ...recurse...
  }
  ```

  It does not distinguish a schema keyword (`{"type":"string","title":"Foo"}`) from a property name inside a `properties` map (`{"properties":{"title":{...}}}`). Both get deleted.
- `:380`: `cleanJSONSchemaForAntigravity` calls it on every tool's parameters.
- `:383-405` (`cleanupRequired`): then removes `title` from `required`, because the property no longer exists. The schema stays internally consistent, so the upstream never complains and the defect is silent.

Call sites that apply it to every tool sent to a Gemini-family upstream:

- `open-sse/translator/request/openai-to-gemini.js:283` (Claude-shaped tools)
- `open-sse/translator/request/openai-to-gemini.js:293` (OpenAI-shaped tools)
- `open-sse/translator/request/openai-to-gemini.js:325` (Gemini-CLI, second pass)
- `open-sse/executors/antigravity.js:294` (Antigravity executor)

Reproduction (run 2026-10-14 against the local 9Router checkout and the real Hermes schema):

```
BEFORE props: assignee,board,body,completion_contract,goal_max_turns,goal_mode,idempotency_key,initial_status,
              max_runtime_seconds,model,parents,priority,project,provider,skills,tenant,title,triage,workspace_kind,workspace_path
              required: ["title","assignee"]
AFTER  props: assignee,board,body,completion_contract,goal_max_turns,goal_mode,idempotency_key,initial_status,
              max_runtime_seconds,model,parents,priority,project,provider,skills,tenant,triage,workspace_kind,workspace_path
              required: ["assignee"]
title survives: false
```

For contrast, Hermes' own native Gemini sanitizer (`oss/hermes-agent/agent/gemini_schema.py:22-25`, `:78-79`)
does this correctly. It recurses into each property value but keeps every property name, and
`sanitize_gemini_schema(KANBAN_CREATE_SCHEMA.parameters)` keeps `title` and `required: ["title","assignee"]`
(verified). The defect exists only in the proxy.

### 3.3 Defect 2: a placeholder `reason` is injected into free-form objects (causes class B)

Same file, `:407-440`, `addPlaceholders`. Any `type: "object"` node with no or empty `properties` gets:

```js
obj.properties = { reason: { type: "string", description: "Brief explanation of why you are calling this tool" } };
obj.required = ["reason"];
```

This runs recursively, so it also hits nested nodes, not just an empty top-level tool schema. The Hermes
deferred-tool bridge `tool_call` (`oss/hermes-agent/tools/tool_search.py:298-317`) declares
`calls[].arguments` as a free-form `{"type": "object"}`. After cleaning:

```
AFTER calls.items.properties.arguments = {"type":"object","description":"Arguments matching the tool schema.",
  "properties":{"reason":{"type":"string",...}},"required":["reason"]}
```

The model is now told that every deferred call's `arguments` must contain `reason`. It complies. Hermes then
validates against the real target schema (`tools/tool_search_validation.py:131`). The DeskRPG
`propose_kanban_card` schema (`oss/deskrpg-hermes-plugin/deskrpg_plugin/card_proposal_tool.py:31-41`) has
`additionalProperties: false`, so the call is rejected. Tools without `additionalProperties: false` accept the
stray `reason` silently, which is why this shows up in only 2 calls. Every deferred call routed through
Gemini still carries the extra field.

### 3.4 Why Hermes surfaces it as "title is required"

`oss/hermes-agent/tools/kanban_tools.py:1107-1111`: `_handle_create` calls `_require_text(args, "title")`
(`:411-415`), which raises `title is required`. The handler is correct. The error text doesn't help the model
recover, because the model's schema has no `title` parameter. In 37 of 51 cases, retries failed or turned into
CLI workarounds.

---

## 4. Affected call sites (blast radius)

### 4.1 Hermes core and plugin tools that lose properties through 9Router today

Method: I loaded the Hermes tool registry (`discover_builtin_tools()` plus `hermes_cli.plugins.discover_plugins()`,
92 tools on this host), then ran every parameter schema through 9Router's `cleanJSONSchemaForAntigravity` and
diffed the property paths. Result: 6 of 92 tools are affected.

| Tool | Owner | Lost property | Required? | Effect |
|---|---|---|---|---|
| `kanban_create` | core `tools/kanban_tools_schemas.py:395-406` | `title` | yes | Hard failure (class A) |
| `propose_kanban_card` | plugin `deskrpg-hermes-plugin/deskrpg_plugin/card_proposal_tool.py:31-33` | `title` | yes | Hard failure when called directly (`title, summary is required`, `card_proposal_tool.py:67-70`) |
| `artifact_save` | plugin `deskrpg-hermes-plugin/deskrpg_plugin/artifacts_tool.py:22` | `title` | no | Artifacts saved without a title, silent degradation |
| `gui_tour` | core `tools/tour_tool.py:92` | `title`, `steps[].title` | no | Tours and steps lose titles, silent |
| `show_tip` | core `tools/tip_tool.py:37` | `title` | no | Tips lose titles, silent |
| `start_chat` | core (`toolsets.py:148`) | `title` | no | New chats untitled, silent |

Also affected: the `tool_call` bridge (`tools/tool_search.py:298-317`). Every deferred call carries an
injected `reason`, and calls to targets with `additionalProperties: false` fail (class B).

### 4.2 Latent exposure (not observed yet)

The strip list also contains `default`, `format`, `examples`, `const`, `optional`, `deprecated`, `if`, `then`,
`else`, `gap`, `padding`, `fontSize`, `fontFamily`, `minProperties` and `maxProperties`. Any MCP server or
plugin tool that names a parameter `format` (common for export and convert tools), `default`, `if` or
`padding` will lose it the same way. I did not enumerate MCP tools, because they are not loaded in this
offline registry dump. A follow-up should rerun the scan on a live gateway (section 6.3).

### 4.3 Non-defect refusals (for completeness)

- `plugins/crew/__init__.py:1057` (`_create_guard`) and `:1077`: these refuse crew-writer cards outside a `/crew` turn (15 events). This is intended.
- The crew contract gate (`crew_card.py prepare_contract`) refuses incomplete contracts (1 event). This is intended.

---

## 5. Why earlier fixes did not hold

- The 2026-10-05 session (`20261005_234011_c2c2fe`) already labelled this "Schema Drift in model proxy". But it worked around it by retrying with `title` added and by falling back to the `hermes kanban create` CLI. The proxy was never fixed, so the defect has recurred on every profile for 2 months.
- Skill-level guidance ("always pass title") can't fix it. The model sees a schema without `title`, and skills can't override the function declaration the upstream enforces.
- The defect is silent end to end. 9Router also removes `title` from `required`, so neither Gemini nor 9Router reports anything, and Hermes sees only a valid-looking call with the field missing.

---

## 6. Permanent solution: architecture design

Design principle: tool schemas are a contract between the tool owner (Hermes or a plugin) and the model. A
transport layer may change keyword vocabulary to fit an upstream's dialect. It must never change the
contract's parameter set. The fix has one root-cause layer and three defense layers.

### 6.1 Layer 1, root-cause fix in 9Router (required)

Repo: `oss/9router` (fork `phuongvm/9router`). Do the work in a dedicated worktree
`O:/workspaces/.worktrees/9router-schema-propnames`, never in the production checkout (Zero-Checkout).

1. Make `removeUnsupportedKeywords` schema-position aware (`open-sse/translator/formats/gemini.js:147-168`):
   - When the current key is `properties` (or `patternProperties` / `$defs` before they are stripped), iterate its entries. Never delete or filter entry names; recurse into each entry's value as a schema.
   - Apply keyword deletion only to schema-keyword positions: the keys of a schema object, not the keys of a `properties` map.
   - Sketch:
     ```js
     function removeUnsupportedKeywords(obj, keywords) {
       if (!obj || typeof obj !== "object") return;
       if (Array.isArray(obj)) { obj.forEach(i => removeUnsupportedKeywords(i, keywords)); return; }
       for (const key of Object.keys(obj)) {
         if (key === "properties" && obj[key] && typeof obj[key] === "object" && !Array.isArray(obj[key])) {
           for (const sub of Object.values(obj[key])) removeUnsupportedKeywords(sub, keywords); // names preserved
           continue;
         }
         if (keywords.includes(key) || key.startsWith("x-")) { delete obj[key]; continue; }
         if (obj[key] && typeof obj[key] === "object") removeUnsupportedKeywords(obj[key], keywords);
       }
     }
     ```
   - Do the same audit for the other recursive walkers in the cleaner (`convertConstToEnum`, `flattenTypeArrays`, `ensureObjectType`, `cleanupRequired`) so none of them treat a property named `const`, `type`, `enum` or `items` as a keyword.
   - Note: Gemini's `Schema` accepts `title` as a keyword (`propertyOrdering`/`title` are in Hermes' own allow-list, `agent/gemini_schema.py:22-25`). Removing `"title"` from the strip list is worth evaluating separately for the native Gemini path. It is not enough on its own, because `default`, `format` and the rest still collide.

2. Limit `addPlaceholders` to the tool's top-level parameters (`:407-440`):
   - Inject `reason` only when the root `parameters` schema is empty, which is the documented Antigravity requirement.
   - For nested free-form objects (`{"type":"object"}` with no properties), never add `required`. Either leave the node as-is if the upstream accepts it, or drop the constraint to a typeless/`STRING` JSON payload per the upstream dialect. Don't invent required fields.

3. Regression tests (`tests/unit/`, vitest):
   - A property named `title` (and `default`, `format`, `examples`, `const`, `if`, `padding`) survives `cleanJSONSchemaForAntigravity` and stays in `required`.
   - A schema keyword `title` on a property value (`{"type":"string","title":"X"}`) is still stripped.
   - A nested free-form `{"type":"object"}` never gains `required: ["reason"]`. The root empty schema still gets the placeholder.
   - A fixture test that loads a snapshot of Hermes tool schemas (`kanban_create`, `tool_call`, `gui_tour`) and asserts the set of property paths is unchanged after cleaning.
   - Judge results with `tests/__baseline__/verify-no-regression.mjs` as the repo requires (`oss/9router/CLAUDE.md`).

4. Deploy to the NUC 9Router instance (:20128). Restarting it is an operator action for the Commander; agents must not restart shared services.

### 6.2 Layer 2, Hermes-side recovery hints (defense, small)

Repo: `oss/hermes-agent`. Worktree `O:/workspaces/.worktrees/hermes-kanban-required-hint`.

- `tools/kanban_tools.py:1111`: when a required schema field is missing, return an error that names the field, its type and its description, plus a short hint: "If your tool schema does not list `title`, the provider route stripped it; pass it anyway." The model then has what it needs to self-correct on the first retry instead of falling back to the CLI. This raises the 14/51 recovery rate but does not replace layer 1.
- Apply the same message shape in `tools/tool_search_validation.py:131` for `additionalProperties` failures. When the unexpected key is exactly `reason`, add "remove `reason`; it was injected by the transport, not by the tool schema".
- One or two invariant tests in `tests/tools/` (dispatch through the registry, not bare functions; run with `scripts/run_tests.sh`), per `oss/hermes-agent/AGENTS.md`.

Rejected alternative: renaming `title` to `task_title` in `kanban_create`. That breaks the public tool API, the
crew and openspec skills, recorded sessions and the CLI parity. It also only moves the problem: any future
parameter named `format` or `default` hits the same proxy bug.

Rejected alternative: having Hermes send `parametersJsonSchema` instead of `parameters`. Hermes talks
OpenAI chat-completions to Headroom/9Router. The Gemini wire shape is chosen inside 9Router, not by Hermes.

### 6.3 Layer 3, schema contract gate (prevents recurrence)

A schema round-trip check that runs on proxy changes and on fleet sync:

1. Producer: a script in `oss/hermes-agent` (or a fleet ops skill) that dumps every registered tool's `parameters` from the live registry, including MCP and plugin tools, to `schemas.json`. The probe used for this report (`dump_schemas.py`, registry plus `discover_plugins()`) is the template.
2. Checker: a script in `oss/9router` that runs each schema through every request translator (`openai-to-gemini`, the Antigravity executor and the others that rewrite schemas) and fails if any property path disappears or any `required` entry is added. `scan_collisions.mjs` is the template.
3. Wire the checker into:
   - 9Router CI and `9router-upstream-sync` (as a new invariant, e.g. 9ROUTER-INV-7: "translators preserve tool parameter names and never add required fields"). This catches upstream merges such as `d3f61aac` that reintroduce the bug.
   - `hermes-fleet-sync` / `hermes-upstream-sync` (rerun the dump whenever Hermes adds tools).

### 6.4 Layer 4, observability (cheap)

- Add a tool-loop classifier signal in Hermes for "required field missing on the first call while the field is declared in the registered schema". If this fires more than N times per day across profiles, it points at schema drift in a proxy, not at model error, and should alert the operator rather than be retried silently.

---

## 7. Recommended follow-up cards (for the coordinator to open; not opened by this worker)

| # | Card | Repo / worktree | Assignee | Verify |
|---|---|---|---|---|
| 1 | Fix 9Router `removeUnsupportedKeywords` property-name stripping and nested `addPlaceholders`, with vitest regressions (6.1) | `oss/9router` -> `.worktrees/9router-schema-propnames` | coder | independent (reviewer) |
| 2 | Deploy the fixed 9Router to the NUC :20128 and run a live round-trip probe (`kanban_create` with `title` via `cb-gemini-flash-high`) | intel-nuc | Commander (service restart) | proof |
| 3 | Hermes required-field and `reason` recovery hints plus invariant tests (6.2) | `oss/hermes-agent` -> `.worktrees/hermes-kanban-required-hint` | coder | independent |
| 4 | Schema contract gate: Hermes schema dump plus 9Router checker plus 9ROUTER-INV-7 in the upstream-sync skill (6.3) | `oss/9router`, `oss/hermes-agent` | coder | independent |
| 5 | Rerun the blast-radius scan with MCP tools loaded on a live gateway (4.2) | ops | coder | proof |

---

## 8. Verification limits (what was not proven)

- Live wire capture: I did not capture an actual request body at :8203 or :20128. Headroom returned 401 to an unauthenticated probe, the router API key is not in this worker's env, and a curl/ssh probe to the NUC was blocked by the user's `approvals.deny` rule. The proxy behavior was proven by running the checked-out 9Router code (`8fcafe9b`) offline against the real Hermes schemas. Whether the deployed 9Router build matches `8fcafe9b` was not verified. The strip entry has been in the code since `d3f61aac` (2026-06-14), and the first observed failure is 2026-08-14, which fits.
- Combo membership: the claim that only Gemini-backed members of `cb-*` combos hit the cleaner (which explains 227 successes against 51 failures on the same model ids) is an inference. Combo definitions on the NUC were not read.
- Headroom (:8203) was assumed to be a pass-through for the `tools` array. I did not audit its schema handling.

---

## 9. Evidence artifacts (scratch, reproducible)

Under `O:/workspaces/_config/agent4070/hermes/profiles/coder/cache/scratch/`:

- `kc_schema.json`: the `kanban_create` parameters from `tools/kanban_tools_schemas.py`
- `repro_9router.mjs`: the single-schema reproduction (section 3.2 output)
- `all_schemas.json`, `scan_collisions.mjs`: the 92-tool blast-radius scan (section 4.1 output)
- `tool_call_schema.json`, `repro_toolcall.mjs`: the `reason` placeholder reproduction (section 3.3 output)
- `t_8600e905_evidence.txt`: the per-failure listing of all 51 class A events (profile, model, session, message id, argument keys)

Rerun: `node <scratch>/repro_9router.mjs <scratch>/kc_schema.json` and
`node <scratch>/scan_collisions.mjs <scratch>/all_schemas.json`.
