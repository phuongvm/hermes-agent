# Teams Connector Config Investigation

**Date:** 2026-05-06  
**Investigator:** OWL  
**Scope:** Why `allowed_users`, `allowed_channels`, `free_response_channels` in `config.yaml` don't work for Microsoft Teams connector, while env vars with raw user IDs do.

---

## Problem Statement

Commander configured Teams connector in `config.yaml`:

```yaml
platforms:
  teams:
    enabled: true
    extra:
      port: 3978
      allowed_users:
        - "hao.nguyen@optimalitypro.com"
        - "phuong@optimalitypro.com"
        - "fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f"
      allowed_channels:
        - "Inspire-Robotics-Hands"
        - "DevOps - Internal Vietnam Team"
      free_response_channels:
        - "Inspire-Robotics-Hands"
```

**Observed behavior:** Only the raw AAD object ID (`fd3fdd74-...`) works, and only when set via `TEAMS_ALLOWED_USERS` env var in `.env`. Email-based usernames and channel names in config.yaml are silently ignored.

---

## Root Cause Analysis

### Finding 1: `allowed_users` in config.yaml is NEVER read by the auth system

**File:** `gateway/run.py` line 3478-3679 (`_is_user_authorized()`)

The authorization system reads `TEAMS_ALLOWED_USERS` from **environment variables only**. For built-in platforms, the env var names are hardcoded in `platform_env_map`. For plugin platforms (Teams is a plugin), it falls back to the platform registry entry's `allowed_users_env` field — which is `TEAMS_ALLOWED_USERS` (set at `plugins/platforms/teams/adapter.py` line 673).

**There is NO code path that reads `config.yaml` `allowed_users` for Teams.**

### Finding 2: config.yaml → env var bridging is INCOMPLETE for Teams

**File:** `gateway/config.py` lines 690-731

The config bridging code copies specific keys from `platforms.<name>` into `extra` dict, but only these keys are bridged:

| Bridged Key | Line | Teams Supported? |
|---|---|---|
| `unauthorized_dm_behavior` | 685-689 | ❌ Not in config |
| `reply_prefix` | 690-691 | ❌ |
| `reply_in_thread` | 692-693 | ❌ |
| `require_mention` | 694-695 | ❌ |
| `free_response_channels` | 696-697 | ❌ (adapter doesn't use it) |
| `mention_patterns` | 698-699 | ❌ |
| `dm_policy` | 700-701 | ❌ |
| `allow_from` | 702-703 | Telegram only |
| `group_policy` | 704-705 | ❌ |
| `group_allow_from` | 706-707 | ❌ |

**`allowed_channels` is NOT in the bridged keys list.** Only Discord (line 771-775) and Slack (via `SLACK_ALLOWED_CHANNELS` at line 860+) have explicit env var mapping.

**`allowed_users` is NOT bridged for non-Telegram platforms.** Only Telegram has the `allow_from` → `TELEGRAM_ALLOWED_USERS` shim at lines 818-822.

### Finding 3: Teams adapter doesn't implement `free_response_channels`

**File:** `plugins/platforms/teams/adapter.py`

While `free_response_channels` IS bridged to `config.extra` by config.py, the Teams adapter **never reads it**. Only Discord (`_discord_free_response_channels()` at line 2842 of `discord.py`) and Slack (`_slack_free_response_channels()` at line 2694 of `slack.py`) have this method.

### Finding 4: Email-based matching doesn't work because Teams sends AAD object IDs

**File:** `gateway/run.py` lines 3662-3664

```python
check_ids = {user_id}
if "@" in user_id:
    check_ids.add(user_id.split("@")[0])
```

The code tries to match email by extracting the local part, but the **actual `user_id` from Teams is the AAD object ID** (UUID like `fd3fdd74-1ba4-4df1-a5ea-55b35de08c5f`), never an email address. The user email (`phuong@optimalitypro.com`) is not available at the authorization check point.

**File:** `plugins/platforms/teams/adapter.py` line 302

```python
user_id = getattr(from_account, "aad_object_id", None) or getattr(from_account, "id", "")
```

The adapter extracts `aad_object_id` or `id` — both are UUIDs, not emails.

---

## Summary Table

| Config Key | config.yaml → extra? | Bridged to env var? | Adapter reads it? | Works? |
|---|---|---|---|---|
| `allowed_users` | ✅ (in extra) | ❌ No mapping | ❌ Reads env var only | ❌ |
| `allowed_channels` | ✅ (in extra) | ❌ No mapping | ❌ Not implemented | ❌ |
| `free_response_channels` | ✅ (bridged to extra) | N/A | ❌ Not implemented | ❌ |

## Why env vars + raw user ID works

1. `TEAMS_ALLOWED_USERS` in `.env` → read directly by `_is_user_authorized()` from `os.getenv()`
2. Teams sends `aad_object_id` as `user_id` → matches the UUID in the allowlist
3. `TEAMS_ALLOW_ALL_USERS=false` in `.env` → doesn't trigger allow-all (line 3562)

---

## Files Involved

| File | Role |
|---|---|
| `gateway/run.py` (line 3478) | `_is_user_authorized()` — reads env vars only |
| `gateway/config.py` (line 690) | Bridging logic — incomplete for Teams |
| `plugins/platforms/teams/adapter.py` (line 60) | `_on_message()` — extracts AAD UUID as user_id |
| `plugins/platforms/teams/adapter.py` (line 673) | Plugin registration — declares `allowed_users_env="TEAMS_ALLOWED_USERS"` |

---

## Recommended Fix

### 1. Add Teams-specific env var mapping in `gateway/config.py`

Add a Teams section similar to Discord/Slack (around line 733):

```python
# Teams settings → env vars (env vars take precedence)
teams_cfg = yaml_cfg.get("teams", {})
if isinstance(teams_cfg, dict):
    teams_extra = teams_cfg.get("extra", {})
    if isinstance(teams_extra, dict):
        # allowed_users → TEAMS_ALLOWED_USERS
        au = teams_extra.get("allowed_users")
        if au is not None and not os.getenv("TEAMS_ALLOWED_USERS"):
            if isinstance(au, list):
                au = ",".join(str(v) for v in au)
            os.environ["TEAMS_ALLOWED_USERS"] = str(au)
        # allowed_channels → TEAMS_ALLOWED_CHANNELS
        ac = teams_extra.get("allowed_channels")
        if ac is not None and not os.getenv("TEAMS_ALLOWED_CHANNELS"):
            if isinstance(ac, list):
                ac = ",".join(str(v) for v in ac)
            os.environ["TEAMS_ALLOWED_CHANNELS"] = str(ac)
```

### 2. Add `free_response_channels` support in Teams adapter

Add a method similar to Discord's `_discord_free_response_channels()`:

```python
def _teams_free_response_channels(self) -> set:
    raw = self.config.extra.get("free_response_channels")
    if not raw:
        return set()
    return {ch.strip() for ch in raw if isinstance(ch, str) and ch.strip()}
```

And check it in `_on_message()` for mention-gating logic.

### 3. Add channel filtering in Teams adapter

In `_on_message()`, after building the source, check `TEAMS_ALLOWED_CHANNELS` env var against `conv.name`:

```python
allowed_channels_csv = os.getenv("TEAMS_ALLOWED_CHANNELS", "").strip()
if allowed_channels_csv and chat_type in ("group", "channel"):
    allowed_channels = {c.strip() for c in allowed_channels_csv.split(",") if c.strip()}
    chat_name = getattr(conv, "name", "") or ""
    if chat_name not in allowed_channels:
        logger.info("[teams] Ignoring message from non-allowed channel: %s", chat_name)
        return
```

### 4. Address email-based user matching

Either:
- **(a)** Document that only AAD object IDs work (not emails), OR
- **(b)** Use MS Graph API to resolve email → AID during auth (heavyweight), OR
- **(c)** Store both AAD ID and email in the Teams adapter's `build_source()` and check both in a custom auth override

Option (a) is simplest. Option (c) is most user-friendly but requires the adapter to override `_is_user_authorization` behavior.

---

## Verification Plan

To confirm these findings:
1. Add `print(f"[DEBUG] user_id={user_id}, allowed={allowed_ids}")` in `_is_user_authorized()` (line 3662 area)
2. Send message from Teams → observe actual `user_id` value (expected: UUID)
3. Check that `TEAMS_ALLOWED_USERS` env var is set (from `.env`)
4. Confirm `allowed_users` from config.yaml does NOT appear in env var
