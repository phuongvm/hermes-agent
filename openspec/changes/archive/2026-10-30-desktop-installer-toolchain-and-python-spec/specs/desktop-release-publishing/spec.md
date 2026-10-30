# Desktop Release Publishing

## ADDED Requirements

### Requirement: Hermes PM Toolchain and Fail-Closed Dependency Repair for Desktop Builds
Every non-`-PushOnly` run of `build-desktop-installer.ps1` SHALL select a Node >=26 / npm >=12 toolchain via the ladder `-ToolchainBinDir`, Hermes PM `source_build_env`, then newest semantic-version tools under `<HermesHome>\tools`, never a hardcoded or ambient Node. It SHALL repair a failing `assert-root-install.mjs` with `node-deps.mjs` or locked `npm ci` (never `npm install`) and fail closed before any build or network request.

#### Scenario: Ladder order is override, PM resolver, then tools fallback
- **WHEN** `-ToolchainBinDir` is absent
- **THEN** the PM `source_build_env` toolchain SHALL be tried first and `<HermesHome>\tools` `node-*`, `npm-*` and `git-*` directories SHALL be used only if it is unavailable or below Node 26

#### Scenario: PM Node 26 beats ambient Node 22 with no legacy env script and no override
- **WHEN** `hermes-env.cmd` is absent, `-ToolchainBinDir` is omitted and a Node 22 directory is first on `PATH`
- **THEN** every recorded Node invocation SHALL use the PM `node-26.x` directory

#### Scenario: Newest semantic version wins
- **WHEN** `<HermesHome>\tools` holds `node-26.7.0-win32-x64`, `node-26.12.1-win32-x64` and `node-22.22.0-win32-x64`
- **THEN** only `node-26.12.1-win32-x64` SHALL be used, ordered by parsed version rather than text

#### Scenario: Explicit toolchain override has top priority
- **WHEN** `-ToolchainBinDir <dir>` is supplied
- **THEN** `<dir>` SHALL be prepended to `PATH` and used instead of the PM tools

#### Scenario: Missing PM Node fails closed before any build
- **WHEN** no Node >=26 or npm >=12 is available through the ladder
- **THEN** the script SHALL exit non-zero naming the required Node version, with zero `npm` or `electron-builder` invocations and zero DSM requests

#### Scenario: Missing workspace dependencies are repaired by the node-deps union install
- **WHEN** `assert-root-install.mjs` exits non-zero before repair
- **THEN** the script SHALL run `node-deps.mjs --source <root> --reuse --workspace ui-tui --workspace web --workspace apps/desktop`, re-run the assertion (rc 0) and continue to build and publish
- **AND** no `npm install` or `npm run install:desktop` SHALL be run

#### Scenario: Only present workspaces are requested and healthy installs skip repair
- **WHEN** a workspace directory such as `web` has no `package.json`, or `assert-root-install.mjs` already exits `0`
- **THEN** the absent workspace SHALL NOT be passed to `node-deps.mjs`, and a healthy install SHALL NOT invoke `node-deps.mjs`

#### Scenario: Missing node-deps script falls back to locked npm ci
- **WHEN** `scripts/build/node-deps.mjs` does not exist and the assertion fails
- **THEN** the script SHALL run `npm ci` as the first npm call and SHALL NOT run `npm install`

#### Scenario: Failed repair propagates before build, packaging or network
- **WHEN** `node-deps.mjs` (or the `npm ci` fallback) exits non-zero, or `assert-root-install.mjs` still fails after repair
- **THEN** the script SHALL exit non-zero with `Desktop dependency installation failed`, with zero renderer-build or `electron-builder` invocations, no staged installer and zero DSM requests

### Requirement: Publisher Python Resolution Rejects Store Alias Stubs
The build script SHALL run the Synology publisher, in default and `-PushOnly` runs, on a Python chosen by `Resolve-PublisherPython`, never ambient `Get-Command python`. `Test-WorkingPython` SHALL accept a candidate only outside `Microsoft\WindowsApps` and when a live probe confirms Python >=3.10. Rungs: `HERMES_PYTHON`/`PYTHON`, checkout `.venv`/`venv`, newest `<HermesHome>\tools\python-*`, then each `python` on `PATH`. With none working it SHALL fail closed before any DSM request.

#### Scenario: Store alias stub on PATH is skipped for the checkout venv
- **WHEN** the only `python` on `PATH` is a `WindowsApps` stub exiting `9009` and the target checkout has a working `.venv\Scripts` interpreter, in a default build or `-PushOnly`
- **THEN** the artifact SHALL be published using the venv interpreter, with the stub message and `9009` absent from output

#### Scenario: Stub first on PATH is skipped for a later working PATH interpreter
- **WHEN** a `WindowsApps` stub precedes a working `python` on `PATH` and neither env var nor venv nor PM Python exists
- **THEN** the working `PATH` interpreter SHALL be used and publication SHALL succeed

#### Scenario: HERMES_PYTHON beats a broken first PATH python
- **WHEN** a non-`WindowsApps` `python` that fails the live probe is first on `PATH` and `HERMES_PYTHON` names a working interpreter
- **THEN** `HERMES_PYTHON` SHALL be used and publication SHALL succeed

#### Scenario: PM-managed Python is used when only a stub remains on PATH
- **WHEN** no env var or checkout venv exists, `PATH` holds only a stub, and `<HermesHome>\tools\python-<version>-win32-<arch>` holds a working interpreter
- **THEN** that interpreter SHALL be used and publication SHALL succeed

#### Scenario: No working Python fails closed before network I/O
- **WHEN** every rung fails the probe or is absent
- **THEN** the script SHALL exit non-zero with `No working Python` and zero DSM requests
- **AND** no secret value SHALL be printed
