# Design: Vaultwarden Credential Resolution for Synology Release Publisher

## Context

`O:/workspaces/_config/agent4070/hermes/config.yaml` already enables the `vaultwarden` plugin (`default_org: Inspire-Robotics`, `default_collection: hermes-agent4070`, `bw.exe` at `O:/workspaces/_config/agent4070/hermes/bin/bw.exe`, server `https://keeper.ptdev.vip`). See `proposal.md` for motivation and `specs/vaultwarden-credential-ladder/spec.md` for normative requirements.

Two technical constraints govern this design:
1. `synology_release_publisher.py` must remain executable under any Python 3.10+ interpreter using standard library modules even when `PyYAML` or `oss/hermes-agent` is not on `PYTHONPATH`.
2. Existing Seam A / Seam B tests pass a temporary `env_file` (`<tmp>/.env`); reading the live host `O:/workspaces/_config/agent4070/hermes/config.yaml` during tests would violate hermetic isolation and invoke the real `bw.exe`.

## Goals / Non-Goals

**Goals:**
- Support `vw://username/...` in `plugins/vaultwarden/__init__.py` so a single Vaultwarden Login item (`SYNOLOGY_DSM`) supplies both `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` across Hermes and standalone scripts.
- Resolve credentials in `synology_release_publisher.py` via a deterministic 3-rung ladder (`Process Env` → `Vaultwarden Live CLI` → `.env Fallback`) with strict fail-closed semantics whenever `SYNOLOGY_DSM_*` is explicitly configured in `secrets.vaultwarden.env`.
- Keep all tests 100% hermetic via public `config_file` / `bw_binary` / `read_user_env` seams and fake `bw` scripts.

**Non-Goals:**
- Storing master passwords or performing interactive password prompts inside unattended release scripts.
- Modifying `O:/workspaces/oss/hermes-agent/` core or `apps/desktop/` files.

## Decisions

### D1: Widen `FIELDS` in `plugins/vaultwarden/__init__.py` to `("notes", "password", "username")`
- **Choice**: Add `"username"` to `FIELDS` and update `extract_field(item, field)` to return `((item.get("login") or {}).get("username") or "").strip()` when `field == "username"`. Direct lookups (`resolve_direct("username", key)`) naturally pass `["get", "username", key]` to `bw --nointeraction`, which Bitwarden CLI natively supports.
- **Rationale**: Eliminates duplicating credentials across two Secure Notes; one standard Login item (`SYNOLOGY_DSM`) serves both Hermes startup secret loading and browser/manual use.

### D2: Self-Contained Stdlib `secrets.vaultwarden` Reader & `bw` Resolver in `synology_release_publisher.py`
- **Choice**:
  - Parse `config.yaml` using `yaml.safe_load` when `yaml` is importable, with a compact stdlib indentation-aware fallback parser for the `secrets.vaultwarden` mapping (`enabled`, `session_env`, `server_url`, `appdata_dir`, `default_org`, `default_collection`, `env`).
  - Resolve `bw` binary via explicit `bw_binary` / `--bw-binary` first, then `<hermes_home>/bin/bw.exe` (or `bw` on POSIX / `PATH`).
  - Resolve `BW_SESSION` (or configured `session_env`) from `env` mapping first, falling back on Windows to `HKCU\Environment` (`winreg`, injectable via `read_user_env` seam for tests), retrying once with fresh `HKCU\Environment` token if `bw` reports `"vault is locked"` (matching `VaultwardenSource.fetch` behavior in `plugins/vaultwarden/__init__.py:402-415`).
  - Verify `bw config server` against `secrets.vaultwarden.server_url` when configured, failing closed on mismatch.
  - **Trade-off (review F-05)**: the grammar/scoping/retry logic is deliberately duplicated because the publisher must run on bare stdlib with no Hermes package on `sys.path`. Drift is controlled by a parity test that parses one shared reference corpus with both implementations (see spec "Shared Reference Grammar Parity").
  - `read_user_env(name) -> str` is an injectable parameter (default: real `HKCU\Environment` reader on Windows, `""` elsewhere) so the stale-session retry is testable without the registry.
  - Resolve `vw://` references using the exact same scoping rules as `plugins/vaultwarden/__init__.py` (`orgs/`, `collections/`, `item/`, `default_org`, `default_collection`, `notes`/`password`/`username`), caching `bw list` JSON responses in-memory for the single publish call so resolving both `username` and `password` from `SYNOLOGY_DSM` issues only one `bw list items` call.

### D3: Per-Variable Authoritative vs. Opportunistic Rung 2 Semantics & Hermetic Path Derivation
- **Choice**:
  - **Hermetic Path Rule** (review F-02): `DEFAULT_HERMES_HOME = Path(__file__).resolve().parent.parent`. Effective home = parent of `config_file`, else parent of `env_file`, else `DEFAULT_HERMES_HOME`. Config = `<home>/config.yaml`; `.env` = `<home>/.env` (explicit `env_file` still wins for `.env`). `bw` = explicit `bw_binary`, else `<home>/bin/{bw.exe,bw.cmd,bw}`, else `PATH` **only** for the untouched production default home. A fixture-derived home with no binary means "bw missing" - the host `bw.exe` and `PATH` are never consulted. The PowerShell wrapper therefore needs both `-HermesConfigPath` and `-BwBinary`.
  - **Resolution is per variable** (review F-01). For each of `SYNOLOGY_DSM_USER` / `SYNOLOGY_DSM_PASS`: process env -> (if `secrets.vaultwarden.enabled` is true) Rung 2 -> `.env`. This preserves the existing mixed-source behaviour (user from env, password from `.env`) and makes partial mappings deterministic.
  - **Authoritative** (variable is a key of `secrets.vaultwarden.env`, enabled): Rung 2 only; every failure class (no session, no `bw`, locked, server mismatch, item not found/ambiguous, invalid ref, empty value) raises `PublishError` without reading `.env` for that variable. Mapping one variable never makes the other authoritative.
  - **Opportunistic** (not mapped): default ref `vw://username/SYNOLOGY_DSM` / `vw://password/SYNOLOGY_DSM`. Every failure class falls through to `.env` (review F-04 matrix: config absent/unreadable, disabled, no session, no `bw`, locked, server mismatch, item missing/ambiguous/empty). The secret-free reason is kept and appended to the final "Missing Synology credential(s)" error.
  - `bw` is only spawned when at least one variable reaches Rung 2; one resolver instance (one `bw` session, cached listings) serves both variables, so `username` + `password` cost a single `bw list items`.

### D4: Public Testing Seams (`codebase-design` + `tdd`)
1. **Plugin Seam (`O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py`)**:
   - Tests `vw.parse_ref`, scoped `VaultwardenSource.fetch`, missing `login.username` warning handling, and direct `bw get username` against `FakeBw`.
2. **Publisher Seam A (`publish_release_artifacts` & CLI in `test_synology_release_publisher.py`)**:
   - Uses a real cross-platform fake `bw` script (`.cmd` wrapping Python on Windows) + temporary `config.yaml` + loopback Fake DSM HTTP server.
   - Verifies Rung 1 precedence (0 `bw` invocations), Rung 2 scoped `SYNOLOGY_DSM` Login item resolution (single `bw list items` call), `HKCU\Environment` stale-session retry, strict fail-closed rejection when `secrets.vaultwarden.env` is mapped and vault is locked (even when `.env` exists), unmapped fallback to `.env`, `server_url` mismatch fail-closed, and zero `BW_SESSION`/password leakage in logs or error strings.
3. **PowerShell Seam B (`build-desktop-installer.ps1 -PushOnly -HermesConfigPath ...`)**:
   - Invokes `pwsh.exe -File build-desktop-installer.ps1 -PushOnly -HermesConfigPath <tmp>/config.yaml -BwBinary <tmp>/vwbin/bw.cmd` (fake `bw`) and verifies end-to-end DSM upload without `.env`.
   - Hermetic-binary guards: a fixture home without `bin/bw*` yields "bw missing" (never host `bw.exe`/`PATH`); the fake `bw` records every invocation so tests assert zero calls when Rung 1 satisfies both variables.

## Risks / Trade-offs

- **[Risk] Operator runs `build-desktop-installer.ps1` while `BW_SESSION` in `HKCU\Environment` is expired (`status: locked`)** → **Mitigation**: Publisher emits a clean, secret-free error: `Vaultwarden vault is locked (session_env=BW_SESSION). Unlock Vaultwarden first or pass -SkipPush for a local-only build.`
- **[Risk] Concurrent execution with documentation card `t_ca610e84`** → **Mitigation**: `t_ca610e84` only touches `O:/workspaces/ai_agents/hermes-agent/docs/*`; this change only touches `_config/agent4070/hermes/plugins/vaultwarden/*`, `_config/agent4070/hermes/services/*`, and `oss/hermes-agent/openspec/changes/synology-publisher-vaultwarden-credentials/*`. Zero shared write paths.
