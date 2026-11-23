# Design

## Context

See `proposal.md` (`Why`) for the verified live `asus-vb` (`hermes-agent v0.23.1`) incident evidence (`errors.log` L3225-3306 and `tui_gateway_crash.log` L152-1223, `sid=e826399e`).

In `tools/terminal_tool_result.py`, `finalize_foreground_result()` wraps cleanly completed foreground command JSON strings in `_CompletedForeground(text, call)` — a `str` subclass with `__slots__ = ("call",)` and a custom `__new__(cls, text: str, call: tuple[str, str, str])`. Its sole consumer is `model_tools._emit_post_tool_call_hook()` (`model_tools.py:718-728`), which calls `confirms_foreground_completion(result, (session_id, turn_id, tool_call_id))` to attach `terminal_foreground_completed=True` on the `post_tool_call` observer hook without polluting the JSON payload seen by the LLM.

When no `transform_tool_result` plugin replaces `result`, the `_CompletedForeground` instance flows out of `model_tools.handle_function_call()` (`model_tools.py:1005-1009`) through `agent/tool_executor.py` (`_publish_sequential_result` -> `_commit_tool_result`) and is appended directly to `messages` as the tool message's `"content"` value. Subsequently, whenever context compression triggers (`post_tool` via `agent/turn_tool_round.py:210` -> `agent/turn_preflight.py:245` `compress_after_tool_results`, or `pre_api` / `turn_start_threshold`), `agent/compression_facade.py:144` runs `snapshot = copy.deepcopy(messages)` inside `_snapshot_worker()` to isolate pooled compression workers from the live transcript.

Because `_CompletedForeground` inherited `str.__reduce_ex__` without defining `__copy__`, `__deepcopy__`, or `__reduce__`, `copy.deepcopy()` delegated to `copyreg._reconstructor(_CompletedForeground, str, text)` (`copyreg.py:104`), invoking `_CompletedForeground.__new__(_CompletedForeground, text)` without the required `call` positional argument and raising `TypeError`. Because the failed compaction left the `_CompletedForeground` object inside `messages`, every later compaction attempt crashed identically and wedged the session.

## Goals / Non-Goals

**Goals:**
- Make `_CompletedForeground` safely copyable (`copy.copy`, `copy.deepcopy`) and serializable (`pickle` across all protocols `0..HIGHEST_PROTOCOL`) without modifying downstream callers.
- Enforce the security invariant that any copied or deserialized `_CompletedForeground` is demoted to a plain `str` so foreground completion evidence can never be forged or replayed across copy/serialization boundaries.
- Preserve exact runtime behavior for the live uncopied result observed by `model_tools._emit_post_tool_call_hook()`.
- Document a verifiable staged rollout from isolated worktree (`wt-fix-completed-foreground`) to `agent4070`, `asus-vb`, and `linux-all`.

**Non-Goals:**
- Changing the JSON wire schema of `terminal` tool responses (never add `terminal_foreground_completed` to JSON).
- Refactoring `agent/compression_facade.py` snapshotting or altering `agent/tool_executor.py` message storage mechanics.
- Modifying any file inside the live primary checkout `O:/workspaces/oss/hermes-agent` during development (`§9 AGENTS.md`).

## Crash Path & Module Seam Architecture

The sequence below shows how a `_CompletedForeground` instance travels from `finalize_foreground_result` into `messages` and hits `agent/compression_facade.py:144`, contrasting the pre-fix crash with the post-fix plain-`str` demotion.

```mermaid
sequenceDiagram
    participant TTR as "tools/terminal_tool_result.py"
    participant MT as "model_tools.py"
    participant TE as "agent/tool_executor.py"
    participant MSG as "messages (live transcript)"
    participant TR as "agent/turn_tool_round.py & turn_preflight.py"
    participant CF as "agent/compression_facade.py:144"

    TTR->>MT: "finalize_foreground_result() -> _CompletedForeground(text, call)"
    MT->>MT: "_emit_post_tool_call_hook (L718-728): confirms_foreground_completion(result, call) == True"
    MT-->>TE: "return _CompletedForeground(text, call) (L1005-1009)"
    TE->>MSG: "_commit_tool_result() appends {role: tool, content: _CompletedForeground}"
    TR->>CF: "compress_after_tool_results(trigger=post_tool) -> _compress_context()"
    CF->>CF: "_snapshot_worker(): snapshot = copy.deepcopy(messages)"

    alt Before Fix (v0.23.1 on asus-vb)
        CF->>TTR: "copyreg._reconstructor(_CompletedForeground, str, text)"
        TTR--xCF: "TypeError: _CompletedForeground.__new__() missing 1 required positional argument: 'call'"
        Note over MSG,CF: "_CompletedForeground stays in messages -> pre_api & turn_start_threshold crash repeatedly (9x, 8 stalled turns)"
    else After Fix (Demotion Hooks on _CompletedForeground)
        CF->>TTR: "_CompletedForeground.__deepcopy__(memo) -> str(self)"
        TTR-->>CF: "plain str (exact JSON text, call attestation stripped)"
        Note over CF: "snapshot succeeds; confirms_foreground_completion(copied, call) == False"
    end
```

## Decisions

### Decision 1: Demote `_CompletedForeground` to Plain `str` in `__copy__`, `__deepcopy__`, and `__reduce__`

Implement three minimal dunder methods directly on `_CompletedForeground` in `tools/terminal_tool_result.py`:
- `__copy__(self) -> str`: returns `str(self)`.
- `__deepcopy__(self, memo: dict[int, Any]) -> str`: returns `str(self)`.
- `__reduce__(self) -> tuple[type[str], tuple[str]]`: returns `(str, (str(self),))`.

**Security Invariant (Why Demotion to Plain `str` Is Mandatory):**
`_CompletedForeground` is an in-process, capability-style attestation bound to one exact live invocation `(session_id, turn_id, tool_call_id)` and checked via strict identity typing (`type(result) is _CompletedForeground and all(call) and result.call == call`).
1. **Preventing Forged Foreground Evidence via Serialization (`__reduce__`)**: If `__reduce__` reconstructed `_CompletedForeground(text, call)`, any pickled/unpickled payload (IPC, cache, disk snapshot, or untrusted stream) could mint a `_CompletedForeground` token carrying an arbitrary `(session_id, turn_id, tool_call_id)` tuple and spoof `terminal_foreground_completed=True` in `post_tool_call` observers. Returning `(str, (str(self),))` guarantees deserialization can only ever yield inert text (`type(restored) is str`).
2. **Preventing Capability Leakage & Replay via Copying (`__copy__`, `__deepcopy__`)**: Once a tool result crosses a copy seam (e.g., `compression_facade._snapshot_worker` deep-copying `messages`, plugin inspection cloning payloads, or transcript archival), its role is strictly inert transcript data. Demoting to plain `str` enforces least privilege and guarantees cloned snapshots cannot carry live completion tokens outside the immediate post-tool observer seam.
3. **Deep-Module Locality (`codebase-design`)**: Concentrating copy/pickle demotion inside `_CompletedForeground` keeps the seam self-contained in `tools/terminal_tool_result.py`. Every current and future caller that passes `messages` or tool results through `copy`, `deepcopy`, or `pickle` automatically inherits crash safety and anti-forgery protection with zero caller churn.

**Alternatives Considered & Rejected:**
- *Reconstruct `_CompletedForeground(str(self), self.call)` on copy/pickle*: Rejected because it violates the security invariant above, allowing cloned or deserialized payloads to forge or replay `confirms_foreground_completion() == True`.
- *Cast `str(function_result)` in `agent/tool_executor.py` before appending to `messages`*: Rejected as the primary fix because `_CompletedForeground` itself would remain crash-prone for any plugin hook, observer, test helper, or future serializer that copies or pickles the return value of `finalize_foreground_result()` or `handle_function_call()`.
- *Make `call` optional in `_CompletedForeground.__new__` (defaulting to `("", "", "")`)*: Rejected because it weakens the constructor invariant (permitting invalid empty-call `_CompletedForeground` instances) and still leaks a subclass type rather than plain `str` across copy/pickle seams.

### Decision 2: Keep `model_tools._emit_post_tool_call_hook` Unchanged as Sole Attestation Consumer

`git grep` confirms `model_tools.py:718-728` is the sole production caller of `confirms_foreground_completion()`. Because `_emit_post_tool_call_hook` executes synchronously on the live uncopied `result` returned by `_execute_tool()` (`model_tools.py:1005-1009`) or `_run_sequential_call()` (`agent/tool_executor.py:1859`), demoting on copy/pickle has zero effect on legitimate foreground completion reporting.

## Risks / Trade-offs

- `[A future caller deep-copies result before calling confirms_foreground_completion]` -> Mitigation: `model_tools.py:718-728` is the sole consumer and runs before any copy/compression step; `test_foreground_evidence_is_bound_to_the_observed_call` and `test_foreground_result_survives_copy_deepcopy_and_pickle_as_plain_str` lock both the live-true and copied-false contracts.
- `[A third-party serializer uses __reduce_ex__(protocol) instead of __reduce__]` -> Mitigation: In Python's `object.__reduce_ex__`, overriding `__reduce__` on a subclass (`_CompletedForeground`) takes precedence over `str.__reduce_ex__` across all protocols `0..pickle.HIGHEST_PROTOCOL`, verified explicitly by the loop `for proto in range(pickle.HIGHEST_PROTOCOL + 1)` in `tests/tools/test_terminal_completion_observer.py`.
- `[Live service disruption or code_skew during fleet rollout]` -> Mitigation: Develop and test strictly inside isolated worktree `O:/workspaces/.worktrees/wt-fix-completed-foreground` (`§9 AGENTS.md`), push verified `HEAD:main` from the worktree, and roll out in three gated waves (`agent4070` -> `asus-vb` with active-user guard -> `linux-all` with dry-run preflight).

## Migration & Staged Rollout Plan

1. **Isolated Worktree Verification (`wt-fix-completed-foreground`)**:
   - Branch `fix/completed-foreground-copy` (based on `origin/main` `79453608e32`).
   - Run ABI-isolated pytest suites (`tests/tools/test_terminal_completion_observer.py` and `tests/agent/test_compression_worker_isolation.py`), strict `openspec validate`, and independent 2-axis review.
2. **Wave 1 — Source Authority (`agent4070`)**:
   - With explicit Commander release approval, commit and push from worktree (`git -C O:/workspaces/.worktrees/wt-fix-completed-foreground push origin HEAD:main`).
   - Apply local cutover to live `O:/workspaces/oss/hermes-agent` via `apply-live-hermes-cutover.ps1 -DeployAgent4070 -SkipDesktop` (or full cutover when Desktop restart is approved).
3. **Wave 2 — Affected Windows Workstation (`asus-vb`)**:
   - Run dry-run probe, then deploy via `fleet-sync.ps1 -Target asus-vb` (using `-Force` only with explicit Commander authorization if `Hermes.exe` is active).
   - Verify deployed `HEAD`, gateway readiness (`gateway_state.json`), and zero recurrence of `_CompletedForeground` `TypeError` in `errors.log` and `tui_gateway_crash.log`.
4. **Wave 3 — Linux Fleet (`linux-all`: `intel_nuc`, `hp_15s`, `hermes-vm-01`, `pi120`, `pi183`)**:
   - Run read-only preflight (`apply-live-hermes-cutover.ps1 -Group linux-all -DryRun`), then execute `apply-live-hermes-cutover.ps1 -Group linux-all`.
   - Verify all 5 Linux nodes report the deployed `code_sha`, active `hermes-gateway`/`hermes-dashboard` units, `/api/status == 200`, and controller OIDC verification.
5. **Rollback**:
   - Pure 9-line Python class-method addition with zero DB/schema/config mutations. If any unexpected regression appears, revert the commit on `origin/main` and re-run the same cutover commands.
