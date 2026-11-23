# Spec Delta

## Purpose

Defines the observable behavior of foreground terminal tool results, ensuring in-process completion attestation remains bound to the live tool invocation while safely demoting to plain string values across copy and serialization operations.

## ADDED Requirements

### Requirement: Foreground completion results survive copy and serialization as plain text
The system SHALL allow any foreground terminal tool result stored in conversation history to be shallow-copied, deep-copied, and pickled across all supported pickle protocols without raising an error, yielding a plain string whose text content is identical to the original tool result.

#### Scenario: Deep-copying a conversation message list containing a foreground terminal result
- **WHEN** a completed foreground terminal tool result is stored in a conversation message list and the list is deep-copied for context compression snapshotting
- **THEN** the deep copy succeeds without raising `TypeError`
- **AND** the copied tool message content has exact runtime type `str` and equals the original JSON text

#### Scenario: Shallow-copying a foreground terminal result
- **WHEN** a caller shallow-copies a completed foreground terminal tool result
- **THEN** the shallow copy succeeds without error
- **AND** the returned value has exact runtime type `str` and equals the original JSON text

#### Scenario: Pickling and unpickling a foreground terminal result across all protocols
- **WHEN** a completed foreground terminal tool result is serialized and deserialized with pickle using every protocol from `0` through the highest supported protocol
- **THEN** every protocol round-trip succeeds without error
- **AND** every deserialized value has exact runtime type `str` and equals the original JSON text

### Requirement: Copied or serialized results MUST NOT retain foreground completion attestation
The system SHALL strip process-local foreground completion attestation whenever a foreground terminal tool result is copied or serialized, so that only the original live uncopied result from the observed tool invocation can confirm foreground completion.

#### Scenario: Live uncopied foreground result confirms completion for its exact call
- **WHEN** a foreground terminal command exits with return code `0` without errors, timeouts, interrupts, sudo authentication failures, directory fallbacks, or environment recreation inside an observed call context
- **THEN** the live uncopied result confirms foreground completion for that exact `(session_id, turn_id, tool_call_id)` tuple
- **AND** the JSON payload itself does not include a `terminal_foreground_completed` field

#### Scenario: Copied or unpickled result cannot forge foreground completion
- **WHEN** a shallow copy, deep copy, or unpickled copy of a confirmed foreground terminal tool result is evaluated by the foreground completion check with the original `(session_id, turn_id, tool_call_id)` tuple
- **THEN** the foreground completion check returns `False`
- **AND** downstream post-tool observers do not emit `terminal_foreground_completed=True` for the copied or deserialized value
