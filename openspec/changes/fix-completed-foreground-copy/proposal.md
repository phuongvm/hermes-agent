# Proposal

## Why

In live production on `asus-vb` (`hermes-agent v0.23.1`, session `20261122_141435_a8835d`), context compaction deterministically crashed with `TypeError: _CompletedForeground.__new__() missing 1 required positional argument: 'call'` at `agent/compression_facade.py:144` (`snapshot = copy.deepcopy(messages)` -> `copyreg.py:104`). Because `_CompletedForeground` (a `str` subclass returned by `finalize_foreground_result()` and retained in `messages`) remained in the live transcript after the first failure, `errors.log` (L3225-3306, `2026-11-23 17:55:31,854`) and `tui_gateway_crash.log` (L152-1223, `sid=e826399e`, 9 consecutive crashes across `post_tool`, `pre_api`, and `turn_start_threshold` triggers between `17:55:32` and `18:06:42`) caused 8 consecutive user turns to stall with zero assistant replies.

## What Changes

- Define copy and pickle demotion hooks (`__copy__`, `__deepcopy__`, `__reduce__`) on `_CompletedForeground` in `tools/terminal_tool_result.py` so shallow copies, deep copies (including `copy.deepcopy(messages)` during context compression snapshotting), and pickle round-trips return a plain `str` carrying the exact serialized JSON payload without the process-local foreground-completion attestation.
- Preserve existing in-process foreground completion verification (`confirms_foreground_completion`) for the live, uncopied result returned to `model_tools._emit_post_tool_call_hook` (`model_tools.py:718-728`), while ensuring copied or deserialized values can never satisfy `confirms_foreground_completion`.
- Add deterministic unit regression coverage in `tests/tools/test_terminal_completion_observer.py` (`test_foreground_result_survives_copy_deepcopy_and_pickle_as_plain_str`) covering `copy.copy`, `copy.deepcopy` inside a `messages` list, and `pickle.dumps`/`pickle.loads` across all pickle protocols.
- Execute a staged fleet rollout (`agent4070` -> `asus-vb` -> `linux-all`) once the fix passes TDD verification and independent 2-axis review.

## Capabilities

### New Capabilities

- `terminal-tool-result`: Behavioral contract for foreground terminal tool result payloads, in-process completion attestation, and safe demotion to plain string values across copy and serialization seams.

### Modified Capabilities

<!-- None: no existing capability specs under openspec/specs/ cover terminal tool result copying or completion attestation. -->

## Impact

- **Code**: `tools/terminal_tool_result.py` (`_CompletedForeground.__copy__`, `__deepcopy__`, `__reduce__`); read-only verification of sole consumer `model_tools.py` (L718-728, L1005-1009) and crash trigger `agent/compression_facade.py` (L144).
- **Tests**: `tests/tools/test_terminal_completion_observer.py` (`test_foreground_result_survives_copy_deepcopy_and_pickle_as_plain_str`) plus compression worker isolation verification (`tests/agent/test_compression_worker_isolation.py`).
- **Systems & Fleet**: Eliminates unrecoverable compaction crashes across all Hermes Agent runtimes (Desktop, TUI, Gateway, headless Linux/WSL satellites) via staged cutover (`agent4070` -> `asus-vb` -> `linux-all`).
- **Breaking Changes**: None. No JSON wire schema fields are added or altered, and no downstream callers require modification.
