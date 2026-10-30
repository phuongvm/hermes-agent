# Exploration: Vaultwarden-Backed Credential Resolution for Synology Release Publisher

> **Date**: 2026-10-29
> **Status**: Finalized (Ready for Turn-1 Crew Dispatch)
> **Project**: `hermes-agent` (`O:/workspaces/oss/hermes-agent`)
> **Targets**:
> - `O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/__init__.py` (+ `test_vaultwarden.py`)
> - `O:/workspaces/_config/agent4070/hermes/services/synology_release_publisher.py` (+ `tests/test_synology_release_publisher.py`)

---

## 1. Problem Statement

`synology_release_publisher.py` currently loads `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` only from process environment variables or plaintext `.env`. Meanwhile, Hermes on `agent4070` already uses the `vaultwarden` `SecretSource` plugin (`https://keeper.ptdev.vip`, `default_org: Inspire-Robotics`, `default_collection: hermes-agent4070`) in `O:/workspaces/_config/agent4070/hermes/config.yaml`. Two gaps block zero-plaintext Vaultwarden integration for `build-desktop-installer.ps1`:
1. **Standalone Process Execution**: Invoking `.\build-desktop-installer.ps1` directly from an operator PowerShell window bypasses the Hermes Gateway/CLI startup secret loader, so `secrets.vaultwarden.env` mappings in `config.yaml` are not injected into `os.environ`.
2. **Missing `username` Field in `vaultwarden` Plugin**: `plugins/vaultwarden/__init__.py` currently restricts `FIELDS = ("notes", "password")`. A standard Vaultwarden Login item (`type=1`, e.g. `SYNOLOGY_DSM`) stores both `login.username` and `login.password`, so `vw://username/SYNOLOGY_DSM` currently raises `RefError: unknown field 'username'`.

---

## 2. Solution & Deep Module Interface

1. **Extend `vaultwarden` Plugin (`vw://username/...` support)**:
   - Widen `FIELDS = ("notes", "password", "username")` in `O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/__init__.py` (and any canonical source copy in `ai_agents` if present).
   - Update `extract_field()` so `field == "username"` extracts `((item.get("login") or {}).get("username") or "").strip()`, and `resolve_direct("username", ...)` invokes `bw get username <id_or_name>`.
2. **3-Rung Credential Resolution Ladder in `synology_release_publisher.py`**:
   - **Rung 1 — Process Environment (`SYNOLOGY_DSM_USER`, `SYNOLOGY_DSM_PASS`)**: Zero-overhead fast path when invoked from an already-hydrated Hermes session.
   - **Rung 2 — Vaultwarden Live Resolver (`config.yaml` → `secrets.vaultwarden` + `bw.exe`)**:
     - Reads `O:/workspaces/_config/agent4070/hermes/config.yaml` (`secrets.vaultwarden`: `enabled`, `env`, `session_env`, `server_url`, `appdata_dir`, `default_org`, `default_collection`).
     - Resolves `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` references (defaulting to `vw://username/SYNOLOGY_DSM` and `vw://password/SYNOLOGY_DSM` when `secrets.vaultwarden.enabled` is true, or explicit `secrets.vaultwarden.env` entries if configured) via `bw.exe --nointeraction` using `BW_SESSION` from process env or Windows `HKCU\Environment`.
     - If Vaultwarden is configured/targeted and `bw` reports a locked vault or missing `BW_SESSION`, fails closed with an actionable unlock message (`bw unlock` / Hermes Vaultwarden unlock) without printing any token or secret.
   - **Rung 3 — `.env` Fallback (`O:/workspaces/_config/agent4070/hermes/.env`)**: Preserved for backward compatibility when neither process env nor Vaultwarden supplies credentials.

---

## 3. Public Testing Seams

1. **Vaultwarden Plugin Seam (`test_vaultwarden.py`)**:
   - Exercises `parse_ref("vw://username/SYNOLOGY_DSM")`, `parse_ref("vw://item/<id>/username")`, scoped collection lookup (`vw://collections/hermes-agent4070/username/SYNOLOGY_DSM`), and `VaultwardenSource.fetch()` through a fake `bw` runner.
2. **Publisher Credential Ladder Seam (`test_synology_release_publisher.py`)**:
   - Exercises `publish_release_artifacts()` / CLI with a fake `bw` binary and temp `config.yaml` (`secrets.vaultwarden`) against the loopback Fake DSM server:
     - Rung 1 process env wins over Vaultwarden and `.env`.
     - Rung 2 resolves `vw://username/SYNOLOGY_DSM` + `vw://password/SYNOLOGY_DSM` from scoped Vaultwarden items (`Inspire-Robotics` / `hermes-agent4070`) and completes DSM upload + verification.
     - Locked vault (`Vault is locked`) fails closed with actionable remediation and zero secret/session leakage in stdout/stderr.
     - Rung 3 `.env` fallback continues to pass all existing 51 Seam A + Seam B tests.

---

## 4. Parallel Execution Safety Analysis

- **In-flight Card `t_ca610e84`**: Currently in `review` (`assignee: reviewer`), touching ONLY `O:/workspaces/ai_agents/hermes-agent/docs/*`.
- **This Workstream**: Touches ONLY `O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/*` and `O:/workspaces/_config/agent4070/hermes/services/*`.
- **Conflict Surface**: **Zero file overlap** (`100% safe to execute in parallel`).

## 5. Out of Scope

- Modifying Electron or Hermes core files under `O:/workspaces/oss/hermes-agent/`.
- Storing master passwords on disk (session tokens come strictly from `BW_SESSION` / Hermes Vaultwarden unlock).
