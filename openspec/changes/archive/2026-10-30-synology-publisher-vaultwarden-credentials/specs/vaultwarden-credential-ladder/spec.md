# Specification: Vaultwarden Credential Ladder

## Purpose

Defines `vw://username` login-field resolution in the Hermes Vaultwarden secret-source plugin and the 3-rung Vaultwarden-backed credential resolution ladder for unattended Synology release publishing.

## ADDED Requirements

### Requirement: Vaultwarden Plugin Username Field Resolution
The Vaultwarden secret-source plugin SHALL support `username` as a valid field selector in addition to `notes` and `password` across unscoped references (`vw://username/<name>`), organization/collection-scoped references (`vw://[orgs/<org>/][collections/<col>/]username/<name>`), and item-ID references (`vw://item/<id>/username`).

#### Scenario: Scoped login item resolves username and password from one vault item
- **WHEN** `secrets.vaultwarden` configures `default_org` and `default_collection` and maps `SYNOLOGY_DSM_USER` to `vw://username/SYNOLOGY_DSM` and `SYNOLOGY_DSM_PASS` to `vw://password/SYNOLOGY_DSM`
- **THEN** the plugin SHALL resolve `SYNOLOGY_DSM_USER` from `login.username` and `SYNOLOGY_DSM_PASS` from `login.password` of the single matching `SYNOLOGY_DSM` item in that scope

#### Scenario: Direct item-id and unscoped username references invoke bw get username
- **WHEN** a reference specifies `vw://item/<id>/username` or unscoped `vw://username/<name>` without org/collection defaults
- **THEN** the resolver SHALL invoke `bw --nointeraction get username <target>` and return the trimmed username string

#### Scenario: Missing or empty login username reports warning without secret leakage
- **WHEN** a scoped item matched by `vw://username/<name>` has no `login` object or an empty `login.username`
- **THEN** `VaultwardenSource.fetch` SHALL omit that variable from `result.secrets` and record a non-secret warning

### Requirement: Three-Rung Publisher Credential Resolution Ladder
The publisher SHALL resolve `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` independently, one variable at a time, via (1) process environment, (2) Vaultwarden CLI per `secrets.vaultwarden` in `config.yaml`, (3) the `.env` entry. A variable satisfied at a higher rung SHALL NOT be looked up at a lower rung, and the two variables MAY come from different rungs.

#### Scenario: Rung 2 applies per variable with explicit or default reference
- **WHEN** a variable is unresolved after Rung 1 and `secrets.vaultwarden.enabled` is `true`
- **THEN** Rung 2 SHALL use the variable's own reference from `secrets.vaultwarden.env` when mapped (authoritative), otherwise `vw://username/SYNOLOGY_DSM` (user) or `vw://password/SYNOLOGY_DSM` (password) (opportunistic)
- **AND** mapping one variable SHALL NOT make the other authoritative

#### Scenario: Process environment satisfies credentials at Rung 1 without invoking bw
- **WHEN** both `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` are non-empty in the process environment
- **THEN** the publisher SHALL authenticate using those values and SHALL NOT spawn the `bw` executable

#### Scenario: Standalone run resolves mapped credentials from Vaultwarden at Rung 2
- **WHEN** `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` are absent from the process environment
- **AND** `secrets.vaultwarden.enabled` is `true` with `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` mapped in `secrets.vaultwarden.env`
- **AND** a valid `BW_SESSION` is present in the process environment or Windows `HKCU\Environment`
- **THEN** the publisher SHALL resolve both credentials via `bw --nointeraction` using the configured scope (`default_org`, `default_collection`, `appdata_dir`, and `server_url`) and complete DSM publication
- **AND** it SHALL issue at most one `bw list items` call for the pair

#### Scenario: Default SYNOLOGY_DSM item resolves when Vaultwarden is enabled without explicit env keys
- **WHEN** `secrets.vaultwarden.enabled` is `true` and `secrets.vaultwarden.env` does not define `SYNOLOGY_DSM_USER` or `SYNOLOGY_DSM_PASS`
- **AND** an unlocked Vaultwarden vault contains Login item `SYNOLOGY_DSM` in the default scope
- **THEN** the publisher SHALL resolve `vw://username/SYNOLOGY_DSM` and `vw://password/SYNOLOGY_DSM` automatically

#### Scenario: Partial explicit mapping is per-variable
- **WHEN** only `SYNOLOGY_DSM_USER` is mapped in `secrets.vaultwarden.env` and `SYNOLOGY_DSM_PASS` is not
- **THEN** `SYNOLOGY_DSM_USER` SHALL be authoritative (Vaultwarden only, fail closed)
- **AND** `SYNOLOGY_DSM_PASS` SHALL follow its own ladder: process environment, then the opportunistic default reference `vw://password/SYNOLOGY_DSM`, then `.env`
- **AND** the symmetric rule SHALL hold when only `SYNOLOGY_DSM_PASS` is mapped

#### Scenario: Mixed process-environment and Vaultwarden sources
- **WHEN** `SYNOLOGY_DSM_PASS` is set in the process environment and `SYNOLOGY_DSM_USER` is authoritatively mapped
- **THEN** the publisher SHALL take the password from the process environment, resolve only the user from Vaultwarden, and complete publication

### Requirement: Authoritative Fail-Closed Vaultwarden Error and Secret Hygiene
A variable that is a key of `secrets.vaultwarden.env` (with `enabled: true`) and is not satisfied by the process environment SHALL be resolved from Vaultwarden only. Any Vaultwarden failure SHALL fail closed with a non-zero exit and remediation text, without consulting `.env` for it and without printing passwords or session tokens.

#### Scenario: Every authoritative failure class fails closed
- **WHEN** an authoritatively mapped variable meets a missing session token (`session_env`, default `BW_SESSION`), missing `bw` executable, locked vault, `server_url` mismatch, unresolvable or ambiguous item, invalid reference, or empty value
- **THEN** the publisher SHALL raise a secret-free error naming the cause and remediation and SHALL NOT read `.env` for that variable

#### Scenario: Explicitly mapped Vaultwarden secret fails closed when vault is locked
- **WHEN** `secrets.vaultwarden.enabled` is `true` and `secrets.vaultwarden.env` maps `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS`
- **AND** `bw` reports `Vault is locked` (even after retrying with Windows `HKCU\Environment` `BW_SESSION`)
- **AND** `.env` contains legacy `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` values
- **THEN** the publisher SHALL exit non-zero naming the locked Vaultwarden state and unlock remediation
- **AND** it SHALL NOT fall back to the `.env` credentials or echo any `BW_SESSION` value
- **AND** it SHALL make no DSM network request

#### Scenario: Stale process session is retried once from the Windows user environment
- **WHEN** `bw` reports `Vault is locked` for the session read from the process environment
- **AND** the `read_user_env` seam returns a different non-empty session value
- **THEN** the publisher SHALL retry the failing `bw` call exactly once with the fresh value and continue on success

### Requirement: Opportunistic Rung 2 Failure Matrix
A variable NOT mapped in `secrets.vaultwarden.env` SHALL be tried against the default `SYNOLOGY_DSM` item when Vaultwarden is enabled, and every Rung 2 failure SHALL fall through to the `.env` rung. The secret-free failure reason SHALL be appended to the final missing-credential error when `.env` also lacks the variable.

#### Scenario: Every opportunistic failure class falls through to .env
- **WHEN** an unmapped variable meets: config file absent or unreadable, `secrets.vaultwarden` absent or not enabled, missing session token, missing `bw` executable, locked vault, `server_url` mismatch, or default item `SYNOLOGY_DSM` not found, ambiguous or empty
- **THEN** the publisher SHALL consult `.env` for that variable and SHALL NOT fail solely because of the Vaultwarden failure

#### Scenario: Unmapped Vaultwarden with locked vault falls back to .env when .env has credentials
- **WHEN** `secrets.vaultwarden.enabled` is `true` but `secrets.vaultwarden.env` does NOT map `SYNOLOGY_DSM_USER` or `SYNOLOGY_DSM_PASS`
- **AND** Vaultwarden is locked while `.env` provides both `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS`
- **THEN** the publisher SHALL fall back to Rung 3 (`.env`) and complete publication

#### Scenario: Unmapped failure with no .env credential reports both causes
- **WHEN** Vaultwarden is enabled and unmapped, Rung 2 fails for any class above, and `.env` lacks the variable
- **THEN** the publisher SHALL fail with a message naming the missing variable(s) and the secret-free Vaultwarden failure reason

#### Scenario: Disabled or absent Vaultwarden configuration never invokes bw
- **WHEN** `secrets.vaultwarden.enabled` is not `true` or the config file does not exist
- **THEN** the publisher SHALL NOT spawn `bw` and SHALL use Rung 3 even if `secrets.vaultwarden.env` contains `SYNOLOGY_DSM_*` keys

### Requirement: Hermetic Config, Binary and User-Environment Seams for Testing
The publisher SHALL expose seams `config_file`, `bw_binary` and `read_user_env` (Python), `--config-file` and `--bw-binary` (CLI), and `-HermesConfigPath` and `-BwBinary` (PowerShell, forwarded to the CLI flags). Fixture-derived homes SHALL never fall back to the host Hermes home, host `bw.exe` or `PATH`.

#### Scenario: Effective home and binary resolution rules
- **WHEN** the effective Hermes home is derived
- **THEN** it SHALL be the parent of `config_file`, else the parent of `env_file`, else the publisher's default Hermes home, with config at `<home>/config.yaml` and `.env` at `<home>/.env` (an explicit `env_file` wins for `.env`)
- **AND** the `bw` binary SHALL be explicit `bw_binary`, else `<home>/bin/bw.exe`, `bw.cmd`, then `bw`, else `bw` on `PATH` only when none of `config_file`, `env_file`, `bw_binary` was supplied
- **AND** `read_user_env` SHALL be a callable `(name: str) -> str` returning the Windows `HKCU\Environment` value or `""`, defaulting to the real registry on Windows and `""` elsewhere

#### Scenario: Explicit env_file test fixture never reads host config.yaml or host bw.exe
- **WHEN** `publish_release_artifacts` is called with `env_file=<temp_dir>/.env` and `config_file=None` and `bw_binary=None`
- **THEN** config lookup SHALL check `<temp_dir>/config.yaml` only and SHALL NOT read the host's live `config.yaml`
- **AND** binary lookup SHALL check `<temp_dir>/bin/` only and SHALL NOT spawn the host `bw.exe` or a `bw` found on `PATH`

#### Scenario: PowerShell wrapper forwards config and binary seams
- **WHEN** `build-desktop-installer.ps1 -PushOnly -HermesConfigPath <tmp>/config.yaml -BwBinary <tmp>/vwbin/bw.cmd` runs with no `SYNOLOGY_DSM_*` in the environment and no `.env`
- **THEN** the publisher SHALL resolve credentials through the fake `bw` and complete publication end-to-end without touching the host config or host `bw.exe`

### Requirement: Shared Reference Grammar Parity
Because the standalone publisher cannot import the Hermes plugin (it must run on stdlib only), it SHALL reimplement the `vw://` grammar and scoping. A test SHALL assert that both implementations parse the same reference corpus (including `username`) to identical `(name, field, org, collection, item_id)` tuples and apply defaults identically, so behaviour drift fails the suite.

#### Scenario: Grammar parity corpus
- **WHEN** the reference corpus (unscoped, `orgs/`, `collections/`, `item/<id>[/field]`, percent-encoded scopes, invalid forms) is parsed by the plugin and by the publisher
- **THEN** both SHALL return identical results or both SHALL reject the reference

