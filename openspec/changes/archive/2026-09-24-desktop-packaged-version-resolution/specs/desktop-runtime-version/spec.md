# Delta Specification: Desktop Runtime Version Resolution

## ADDED Requirements

### Requirement: Version Resolution Context Declares Packaging Mode
The desktop version resolution ladder SHALL accept an explicit `isPackaged` boolean input alongside the existing inputs (local source tree root, bundled install stamp, Electron app version). When `isPackaged` is omitted, the ladder SHALL behave as development mode (`isPackaged === false`).

#### Scenario: Packaging mode is an explicit input
- **WHEN** the ladder is invoked with a context that includes `isPackaged`
- **THEN** the value of `isPackaged` alone determines which rung order is applied
- **AND** no filesystem probing or environment inspection is used to infer packaging mode inside the ladder

#### Scenario: Omitted packaging mode defaults to development order
- **WHEN** the ladder is invoked with a context that does not include `isPackaged`
- **THEN** the ladder SHALL apply the development-mode rung order (local source tree first)

### Requirement: Packaged Builds Resolve Version From Bundled Install Stamp First
When `isPackaged === true`, the ladder SHALL evaluate the bundled install stamp BEFORE the local source tree. A stamp that carries a valid semantic `version` and a valid 8-hex-character `shortCommit` is authoritative and SHALL produce the client version string formatted as `${version} (${shortCommit})` with a ` [DIRTY]` suffix when the stamp is marked dirty.

#### Scenario: Packaged install with a local Python runtime present
- **WHEN** `isPackaged === true`
- **AND** a valid install stamp with `version` `0.21.3` and a valid `shortCommit` is provided
- **AND** a local source tree root is provided whose `hermes_cli/__init__.py` declares `__version__ = "0.20.0"`
- **THEN** the resolved version SHALL be `0.21.3 (<shortCommit>)`
- **AND** the local source tree SHALL NOT be read

#### Scenario: Packaged install with a dirty stamp
- **WHEN** `isPackaged === true`
- **AND** a valid install stamp with `dirty: true` is provided
- **THEN** the resolved version SHALL end with ` [DIRTY]`

#### Scenario: Packaged install falls back to local source tree when stamp is absent
- **WHEN** `isPackaged === true`
- **AND** no install stamp is provided
- **AND** a local source tree root is provided whose `hermes_cli/__init__.py` declares a version
- **THEN** the resolved version SHALL be the `__version__` value read from that source tree

#### Scenario: Packaged install falls back when stamp lacks version or shortCommit
- **WHEN** `isPackaged === true`
- **AND** an install stamp is provided whose `version` is null OR whose `shortCommit` is null
- **THEN** the stamp SHALL be treated as invalid for resolution purposes
- **AND** resolution SHALL continue to the local source tree, then the Electron app version

#### Scenario: Packaged install falls back to Electron app version as last resort
- **WHEN** `isPackaged === true`
- **AND** no valid install stamp is available
- **AND** no readable local source tree version is available
- **THEN** the resolved version SHALL be the Electron app version
- **AND** if no app version is provided, the resolved version SHALL be `0.0.0`

### Requirement: Development Builds Preserve Source-Tree-First Resolution
When `isPackaged === false`, the ladder SHALL evaluate the local source tree FIRST, then the bundled install stamp, then the Electron app version. This order is unchanged from current behavior.

#### Scenario: Development run prefers the checkout version over a stale local build stamp
- **WHEN** `isPackaged === false`
- **AND** a local source tree root is provided whose `hermes_cli/__init__.py` declares `__version__ = "0.21.0.dev1"`
- **AND** a valid install stamp with `version` `0.21.0` is provided
- **THEN** the resolved version SHALL be `0.21.0.dev1`

#### Scenario: Development run without a source tree uses the stamp
- **WHEN** `isPackaged === false`
- **AND** the local source tree root does not contain a readable `hermes_cli/__init__.py`
- **AND** a valid install stamp is provided
- **THEN** the resolved version SHALL be the formatted stamp version

### Requirement: Production Call Site Supplies the Real Packaging Flag
The Electron main process SHALL pass its actual packaged-state flag (the same flag that gates installer-only behaviors such as bundle-swap detection) into the version resolution ladder at the single production call site that feeds `hermes:version`, the About panel, and skew messaging.

#### Scenario: Installed Windows build reports its own version in Remote Mode
- **WHEN** Hermes Desktop is running from an installer build whose bundled stamp declares `0.21.3`
- **AND** `%LOCALAPPDATA%\hermes\hermes-agent\hermes_cli\__init__.py` declares `0.20.0`
- **AND** the app is in Remote Mode
- **THEN** the `hermes:version` IPC `appVersion` field SHALL begin with `0.21.3`
- **AND** the status bar client label SHALL read `client v0.21.3 (<shortCommit>)`

#### Scenario: Packaging flag override for verification
- **WHEN** the process is not an installer build
- **AND** the environment variable `HERMES_DESKTOP_IS_PACKAGED` is set to a non-empty value
- **THEN** the ladder SHALL be invoked with `isPackaged === true`

### Requirement: Version Resolution Change Does Not Alter Sibling Contracts
Reordering version resolution SHALL NOT change the `hermes:version` IPC response shape, the `hermesRoot` value it reports, `resolveUpdateRoot()` semantics, install stamp loading/validation rules, or bundle-skew detection inputs.

#### Scenario: IPC response shape is unchanged
- **WHEN** the renderer invokes `hermes:version`
- **THEN** the response SHALL still contain `appVersion`, `electronVersion`, `nodeVersion`, `platform`, `hermesRoot`, `bundleOutOfSync`, `bundleCommitsBehind`, and `bundleSwapPending`
- **AND** only the value of `appVersion` differs from pre-change behavior in the packaged-with-local-runtime case

#### Scenario: Existing ladder and stamp tests remain green
- **WHEN** the existing runtime-version test suite is executed after the change
- **THEN** all previously passing stamp validation, formatting, and ladder tests SHALL still pass without modification of their assertions
