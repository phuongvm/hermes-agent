# Proposal: Vaultwarden Credential Resolution for Synology Release Publisher

## Why

`synology_release_publisher.py` currently requires `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` in process environment variables or plaintext `.env`, while Hermes on `agent4070` is standardizing secret management on Vaultwarden (`https://keeper.ptdev.vip`, `secrets.vaultwarden` in `config.yaml`). Supporting `vw://username/<item>` in the Hermes `vaultwarden` plugin and adding a 3-rung Vaultwarden-aware credential resolution ladder to `synology_release_publisher.py` eliminates plaintext NAS passwords on disk and works identically whether invoked inside Hermes or standalone from PowerShell (`.\build-desktop-installer.ps1`).

## What Changes

- Extend the Hermes `vaultwarden` secret-source plugin (`O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/__init__.py`) to support the `username` field across all `vw://` reference forms (`vw://username/<name>`, `vw://orgs/<org>/collections/<col>/username/<name>`, `vw://collections/<col>/username/<name>`, and `vw://item/<id>/username`), alongside existing `notes` and `password` fields.
- Upgrade credential loading in `O:/workspaces/_config/agent4070/hermes/services/synology_release_publisher.py` to an observable 3-rung Credential Resolution Ladder:
  1. **Rung 1 — Process Environment** (`SYNOLOGY_DSM_USER`, `SYNOLOGY_DSM_PASS`).
  2. **Rung 2 — Vaultwarden Live Resolver** (`config.yaml` `secrets.vaultwarden` + `bw` CLI with `BW_SESSION` from process env or Windows `HKCU\Environment`), supporting both explicit `secrets.vaultwarden.env` mappings (strict fail-closed) and default Login item `SYNOLOGY_DSM` (`vw://username/SYNOLOGY_DSM` + `vw://password/SYNOLOGY_DSM`).
  3. **Rung 3 — Legacy `.env` Fallback** (`O:/workspaces/_config/agent4070/hermes/.env`) when Vaultwarden is disabled or unmapped.
- Expose narrow public configuration/test seams (`--config-file`, `--bw-binary` on `synology_release_publisher.py` CLI and `config_file`, `bw_binary`, `read_user_env` parameters on `publish_release_artifacts`, plus `-HermesConfigPath` and `-BwBinary` on `build-desktop-installer.ps1`, per-variable authoritative/opportunistic resolution) so Vaultwarden resolution and locked-vault failures are 100% verifiable without live vault mutations or private monkeypatching.
- Add comprehensive automated tests in `O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py` and `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py`.

## Capabilities

### New Capabilities
- `vaultwarden-credential-ladder`: `vw://username` reference support in the Vaultwarden secret-source plugin and 3-rung Vaultwarden-backed credential resolution for standalone release publishing.

### Modified Capabilities
None.

## Impact

- **Vaultwarden Plugin (`O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/`)**:
  - `__init__.py`: Widen `FIELDS` to `("notes", "password", "username")` and extract `login.username`.
  - `test_vaultwarden.py`: Unit tests for `vw://username` parsing, scoped collection extraction, and direct `bw get username` resolution.
- **Release Publisher & Build Script (`O:/workspaces/_config/agent4070/hermes/services/`)**:
  - `synology_release_publisher.py`: Add stdlib-compatible `secrets.vaultwarden` config reader, `bw` CLI runner (`BW_SESSION` + `HKCU\Environment` retry on locked vault + `server_url` verification + `appdata_dir`), 3-rung credential ladder, and CLI flags `--config-file` / `--bw-binary`.
  - `build-desktop-installer.ps1`: Forward optional `-HermesConfigPath` / `-BwBinary` to `synology_release_publisher.py` as `--config-file` / `--bw-binary`.
  - `tests/test_synology_release_publisher.py`: Fake `bw` executable tests across Seam A and Seam B (preserving all 51 existing tests green).
- **In-Tree Repository (`O:/workspaces/oss/hermes-agent`)**:
  - Zero edits to `apps/desktop/` or core Python files; zero overlap with concurrent documentation files under `O:/workspaces/ai_agents/hermes-agent/docs/`.
