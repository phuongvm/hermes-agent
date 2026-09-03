# Teams Connector Review Findings

**Date:** 2026-05-06  
**Reviewer:** Agent Reviewer  
**Project:** `hermes-agent`  
**Scope:** Review and verify the two Teams connector investigation artifacts against the current codebase, runtime config loader, and local docs.

## Context

No active OpenSpec change exists in this project. Under the normal workflow, change-specific findings would live in `openspec/changes/<change>/reviews/`. Because there is no active change yet, these findings are recorded as an independent audit in `openspec/workspace/audits/2026-05-06/`.

Artifacts reviewed:

- [`2026-05-06-teams-config-investigation.md`](/home/ubuntu/workspaces/oss/hermes-agent/openspec/workspace/explorations/2026-05-06-teams-config-investigation.md)
- [`2026-05-06-teams-connector-config-investigation-by-hermes.md`](/home/ubuntu/workspaces/oss/hermes-agent/openspec/workspace/explorations/2026-05-06-teams-connector-config-investigation-by-hermes.md)
- [`config.yaml`](/home/ubuntu/workspaces/_config/intel-nuc/.hermes/config.yaml)

## Findings

### 1. Hermes remediation proposal targets the wrong config path

- **Severity:** High
- **Artifact:** [`2026-05-06-teams-connector-config-investigation-by-hermes.md`](/home/ubuntu/workspaces/oss/hermes-agent/openspec/workspace/explorations/2026-05-06-teams-connector-config-investigation-by-hermes.md:124)
- **Issue:** The proposed patch reads `yaml_cfg.get("teams")`, but the real config is stored at `platforms.teams.extra`.
- **Evidence:**
  - User config uses `platforms.teams.extra.allowed_users`, `allowed_channels`, and `free_response_channels`: [`config.yaml`](/home/ubuntu/workspaces/_config/intel-nuc/.hermes/config.yaml:468)
  - The loader deep-merges `platforms.*` blocks first: [`gateway/config.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/config.py:661)
- **Impact:** If implemented as written, the patch would still miss the user's current Teams config.

### 2. Antigravity overstates that Teams config is "ignored"

- **Severity:** Medium
- **Artifact:** [`2026-05-06-teams-config-investigation.md`](/home/ubuntu/workspaces/oss/hermes-agent/openspec/workspace/explorations/2026-05-06-teams-config-investigation.md:8)
- **Issue:** The conclusion is directionally correct for authorization behavior, but inaccurate at the config-loading layer.
- **Evidence:**
  - Runtime probe after `load_gateway_config()`:
    - `teams_enabled=True`
    - `teams_extra={"allowed_channels":["Inspire-Robotics-Hands","DevOps - Internal Vietnam Team"],"allowed_users":["hao.nguyen@optimalitypro.com","phuong@optimalitypro.com","fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f"],"free_response_channels":["Inspire-Robotics-Hands"],"port":3978}`
    - `TEAMS_ALLOWED_USERS=None`
  - Config merge path: [`gateway/config.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/config.py:661)
  - Auth lookup path only reads env allowlists: [`gateway/run.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/run.py:3593)
- **Impact:** The real bug is not "config not loaded"; it is "config loaded but not consumed by Teams auth/channel policy paths."

### 3. Email-based allowlists do not work because Teams auth matches on AAD object ID

- **Severity:** Medium
- **Artifacts:** Both investigations captured this correctly
- **Evidence:**
  - Teams adapter builds `user_id` from `aad_object_id` first: [`plugins/platforms/teams/adapter.py`](/home/ubuntu/workspaces/oss/hermes-agent/plugins/platforms/teams/adapter.py:302)
  - Gateway auth checks `source.user_id` against allowlists: [`gateway/run.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/run.py:3662)
  - Runtime probe:
    - `TEAMS_ALLOWED_USERS=<uuid>` -> `uuid_allowed=True`
    - `TEAMS_ALLOWED_USERS=phuong@optimalitypro.com` -> `email_allowed_against_uuid_user_id=False`
- **Impact:** A user-friendly email allowlist is not a supported contract today. The current working contract is AAD object IDs.

### 4. The current repo does not implement Teams `allowed_channels` or `free_response_channels`

- **Severity:** Medium
- **Artifact:** Hermes report is materially correct on this point
- **Evidence:**
  - Teams adapter only sets `chat_name` and forwards the event; no channel allowlist or free-response policy is enforced: [`plugins/platforms/teams/adapter.py`](/home/ubuntu/workspaces/oss/hermes-agent/plugins/platforms/teams/adapter.py:260)
  - Repo search on Teams adapter only found `chat_name=getattr(conv, "name", None) or ""`: [`plugins/platforms/teams/adapter.py`](/home/ubuntu/workspaces/oss/hermes-agent/plugins/platforms/teams/adapter.py:307)
  - Discord and Slack have explicit helpers for free-response behavior, Teams does not:
    - [`gateway/platforms/discord.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/platforms/discord.py:2842)
    - [`gateway/platforms/slack.py`](/home/ubuntu/workspaces/oss/hermes-agent/gateway/platforms/slack.py:2694)
- **Impact:** Those keys in `platforms.teams.extra` are currently inert.

### 5. Official Teams docs support AAD-ID env configuration, not the user’s desired YAML/email contract

- **Severity:** Warning
- **Issue:** The user expectation is reasonable from a UX perspective, but it is not what the current docs or code promise.
- **Evidence:**
  - Teams config.yaml example documents credentials and port only: [`website/docs/user-guide/messaging/teams.md`](/home/ubuntu/workspaces/oss/hermes-agent/website/docs/user-guide/messaging/teams.md:134)
  - Teams auth docs explicitly reference `TEAMS_ALLOWED_USERS` with AAD object IDs: [`website/docs/user-guide/messaging/teams.md`](/home/ubuntu/workspaces/oss/hermes-agent/website/docs/user-guide/messaging/teams.md:129)
  - Troubleshooting and security sections repeat the same contract: [`website/docs/user-guide/messaging/teams.md`](/home/ubuntu/workspaces/oss/hermes-agent/website/docs/user-guide/messaging/teams.md:194), [`website/docs/user-guide/messaging/teams.md`](/home/ubuntu/workspaces/oss/hermes-agent/website/docs/user-guide/messaging/teams.md:203)
- **Impact:** This is partly a product gap and partly a documentation/expectation mismatch.

## Verdict

### Antigravity

- **Strengths:** Correctly identified the UUID-vs-email auth issue and the lack of Teams channel policy support.
- **Weaknesses:** Overstated the loader problem by saying the config is ignored wholesale.
- **Assessment:** Useful but incomplete. Safe as orientation, not strong enough as implementation guidance.

### Hermes

- **Strengths:** Stronger tracing through `_is_user_authorized()`, plugin registry auth env mapping, and the current Teams adapter behavior.
- **Weaknesses:** Proposed fix path is wrong for the user's actual config shape; channel-name matching recommendation is not yet a proven contract.
- **Assessment:** Better root-cause analysis, but the remediation plan needs correction before implementation.

## Recommended Next Step

Before any implementation work, use a single remediation exploration as the source of truth for:

1. the exact config path to support,
2. the supported identity contract for users,
3. the supported identity contract for channels,
4. whether Teams mention-gating must be formalized before `free_response_channels` is added.
