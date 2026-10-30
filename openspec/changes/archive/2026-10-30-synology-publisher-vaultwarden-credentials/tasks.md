# Tasks: Vaultwarden Credential Resolution for Synology Release Publisher

## 1. Tracer Bullet 1: Vaultwarden Plugin `vw://username` Support & Unit Tests

- [x] 1.1 [Blocked by: None] Extend `O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/__init__.py` to support `username` in `FIELDS` and `extract_field()` across scoped, unscoped, and item-ID `vw://` references, and add unit test coverage in `O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py`. Proof: `python -m pytest "O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py" -q` exits 0.

## 2. Tracer Bullet 2: Publisher 3-Rung Vaultwarden Credential Ladder & Seam A/B Tests

- [x] 2.1 [Blocked by: 1.1] Implement the 3-rung Credential Resolution Ladder (`Process Env` -> `Vaultwarden Live CLI from config.yaml` -> `.env Fallback`), hermetic `config_file`/`bw_binary`/`read_user_env` seams, and `--config-file`/`--bw-binary` CLI flags (forwarded by `-HermesConfigPath`/`-BwBinary` in the PowerShell wrapper), per-variable authoritative/opportunistic semantics and a plugin-vs-publisher grammar parity test in `O:/workspaces/_config/agent4070/hermes/services/synology_release_publisher.py` and `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`, with full Seam A and Seam B fake-`bw` tests in `O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py` (keeping all existing 51 tests green). Proof: `python -m pytest "O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py" "O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py" -q` exits 0.

## 3. Tracer Bullet 3: Independent Verification, Zero-Secret Audit & Strict Spec Gate

- [x] 3.1 [Blocked by: 2.1] Independently execute the combined Vaultwarden plugin + Synology publisher Seam A/B test suite, verify zero secret/token leakage and zero edits to `O:/workspaces/oss/hermes-agent/apps/desktop/`, pass strict OpenSpec validation, and write `reviews/findings-implementation-review.md`. Proof: `"C:/nvm4w/nodejs/openspec.exe" validate synology-publisher-vaultwarden-credentials --strict && python -m pytest "O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py" "O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py" -q` exits 0.

## Workflow follow-up

- Sync `specs/vaultwarden-credential-ladder/spec.md` into `openspec/specs/vaultwarden-credential-ladder/spec.md` via `openspec-sync-specs` after independent verification passes.
- Archive `synology-publisher-vaultwarden-credentials` via `openspec archive synology-publisher-vaultwarden-credentials --skip-specs -y` after explicit Commander approval.
