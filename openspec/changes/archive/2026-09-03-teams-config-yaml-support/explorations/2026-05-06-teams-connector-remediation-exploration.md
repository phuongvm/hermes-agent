# Teams Connector Remediation Exploration

**Date:** 2026-05-06  
**Author:** Agent Reviewer  
**Purpose:** Consolidate corrected findings into a single implementation-oriented exploration artifact that another agent can use as the basis for a proposal or patch.

## Problem Restatement

The current Teams connector behavior is inconsistent from the user's perspective:

- `platforms.teams.extra.allowed_users` in `config.yaml` is loaded into runtime config, but it does not affect authorization.
- `TEAMS_ALLOWED_USERS` in `.env` works only when the list contains Azure AD object IDs.
- `platforms.teams.extra.allowed_channels` and `free_response_channels` are accepted by config loading but are not enforced anywhere in the Teams adapter.

## Verified Current Behavior

### Config loading

The loader does ingest the Teams block under `platforms.teams.extra`.

Evidence:

- User config: [`config.yaml`](/home/ubuntu/workspaces/_config/intel-nuc/.hermes/config.yaml:468)
- Merge logic: [`gateway/config.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/config.py:661)

Observed runtime result from a read-only probe:

```text
teams_enabled= True
teams_extra= {"allowed_channels":["Inspire-Robotics-Hands","DevOps - Internal Vietnam Team"],"allowed_users":["hao.nguyen@optimalitypro.com","phuong@optimalitypro.com","fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f"],"free_response_channels":["Inspire-Robotics-Hands"],"port":3978}
TEAMS_ALLOWED_USERS= None
TEAMS_ALLOW_ALL_USERS= None
```

### Authorization

Teams authorization currently works through the plugin registry env-var contract, not by reading `config.extra`.

Evidence:

- Plugin declares `allowed_users_env="TEAMS_ALLOWED_USERS"`: [`plugins/platforms/teams/adapter.py`](/home/ubuntu/workspaces/oss/hermes-agent/plugins/platforms/teams/adapter.py:660)
- Gateway auth reads env allowlists and plugin registry metadata: [`gateway/run.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/run.py:3478)

Observed runtime result:

```text
TEAMS_ALLOWED_USERS=<uuid>                    -> uuid_allowed=True
TEAMS_ALLOWED_USERS=phuong@optimalitypro.com -> email_allowed_against_uuid_user_id=False
```

### User identity

Teams message events currently identify the sender using `aad_object_id` first.

Evidence:

- [`plugins/platforms/teams/adapter.py`](/home/ubuntu/workspaces/oss/hermes-agent/plugins/platforms/teams/adapter.py:302)
- [`tests/gateway/test_teams.py`](/home/ubuntu/workspaces/oss/hermes-agent/tests/gateway/test_teams.py:499)

Implication:

- The practical supported auth contract today is **AAD object ID**, not email address.

### Channel policy

The adapter forwards `chat_name`, but there is no Teams-specific channel allowlist or free-response gating.

Evidence:

- Event construction: [`plugins/platforms/teams/adapter.py`](/home/ubuntu/workspaces/oss/hermes-agent/plugins/platforms/teams/adapter.py:305)
- No Teams references to `allowed_channels` or `free_response_channels` beyond setting `chat_name`: repo search result
- Comparable implementations exist for Discord and Slack:
  - [`gateway/platforms/discord.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/platforms/discord.py:3343)
  - [`gateway/platforms/slack.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/platforms/slack.py:1685)

## Corrected Root Cause Summary

This is not one bug. It is three separate product gaps:

1. **Teams auth consumes env allowlists, not `platforms.teams.extra.allowed_users`.**
2. **Teams sender identity is AAD-object-ID-based, so email strings do not match current auth semantics.**
3. **Teams channel policy settings are not implemented in the adapter.**

## Remediation Strategy

### Option A: Minimal safe fix

Goal: make the user's existing YAML config work with the current identity model.

Scope:

1. Support `platforms.teams.extra.allowed_users` as a source for Teams authorization.
2. Keep the supported identity contract as AAD object IDs only.
3. Document clearly that Teams allowlists currently require AAD object IDs.

Implementation direction:

- Use the already-loaded `PlatformConfig.extra` for Teams instead of trying to read a second raw YAML path.
- Either:
  - synthesize `TEAMS_ALLOWED_USERS` from `config.platforms[Platform("teams")].extra["allowed_users"]`, or
  - extend auth logic to consult plugin config extra directly for Teams.

Recommendation:

- Prefer **reading from `config.extra`** over adding more ad hoc YAML-to-env shims. The config is already loaded correctly; the bug is the missing consumer path.

### Option B: Add Teams channel gating

Goal: make `allowed_channels` meaningful.

Scope:

1. Decide the contract:
   - **Recommended:** channel/conversation IDs
   - **Not recommended as primary contract:** human-readable channel names
2. Implement a Teams-specific allowlist gate in the adapter before `handle_message()`.

Reasoning:

- `conversation.name` may be useful for display, but name-based policy is brittle.
- IDs are stable and align with how other platforms usually enforce channel policy.

### Option C: Add email-friendly user matching

Goal: allow `allowed_users` entries like `phuong@optimalitypro.com`.

Possible approaches:

1. **Documentation-only:** explicitly state "AAD object IDs only".
2. **Payload-based expansion:** if the Teams SDK exposes a stable email/UPN field in message events, populate it into the source and expand auth checks.
3. **Graph lookup:** resolve AAD object ID -> email via Microsoft Graph.

Recommendation:

- Do **not** start with Graph lookup unless product explicitly wants that complexity.
- First verify whether the incoming SDK payload already contains a stable UPN/email field. If not, keep v1 as AAD-ID-only.

## Open Questions That Must Be Answered Before Implementation

### 1. What is the supported contract for Teams user allowlists?

Choose one:

- AAD object IDs only
- AAD object IDs plus email aliases
- Email aliases only

Reviewer recommendation:

- **AAD object IDs only for the first fix**, with documentation improvement.

### 2. What is the supported contract for Teams channel allowlists?

Choose one:

- Conversation IDs
- Channel names
- Both, with IDs preferred

Reviewer recommendation:

- **Conversation IDs**, optionally with channel names as best-effort aliases later.

### 3. Does Teams need explicit mention-gating before `free_response_channels` makes sense?

Current repo evidence does not prove where Teams mention gating lives end-to-end.

Implication:

- Before implementing `free_response_channels`, verify whether:
  - the SDK only delivers messages already directed at the bot, or
  - the adapter must explicitly enforce mention-only behavior in channels.

## Recommended Handoff to Antigravity

If Antigravity is going to implement from this exploration, the work should be framed as:

1. **Fix Teams allowlist consumption**
   - Source from `PlatformConfig.extra`
   - Preserve env vars as explicit override
2. **Define and implement Teams channel allowlist contract**
   - Prefer IDs
3. **Decide whether email aliases are in scope**
   - If not, update docs and error messaging
4. **Add tests**
   - config-driven Teams allowlist
   - Teams auth success on UUID
   - Teams auth failure on email when UUID-only mode is active
   - Teams channel allowlist behavior

## Suggested Acceptance Criteria

### AC1

Given `platforms.teams.extra.allowed_users` is set to a list of AAD object IDs in `config.yaml`, Teams messages from those IDs are authorized without requiring `TEAMS_ALLOWED_USERS` in `.env`.

### AC2

Given `platforms.teams.extra.allowed_users` contains only email addresses, behavior is either:

- explicitly unsupported and documented, or
- supported through a verified, deterministic identity expansion path.

### AC3

Given `platforms.teams.extra.allowed_channels` is set, the Teams adapter processes only messages from those allowed channels according to the chosen contract.

### AC4

If `free_response_channels` is retained as a Teams feature, its behavior is defined against a verified mention-gating model and covered by tests.
