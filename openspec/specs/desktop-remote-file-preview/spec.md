## Purpose

Defines reliable and secure file preview behavior when Hermes Desktop reads Gateway-owned files across host and operating-system boundaries.

## Requirements

### Requirement: Gateway-owned preview paths preserve remote authority
When a file path originates from a remote Gateway Files or Review tree, Hermes Desktop SHALL treat the Gateway as authoritative for file existence and metadata and SHALL NOT reject or rewrite the path based on the Desktop host filesystem.

#### Scenario: Remote-only Windows file opens from Files tree
- **WHEN** a Windows Gateway lists `O:\workspace\project\README.md`, the file does not exist on the Desktop host, and the user opens it from the remote Files tree
- **THEN** Desktop requests the listed path from the active Gateway unchanged and renders the returned file

#### Scenario: Remote-only review file opens in preview
- **WHEN** a remote Review tree supplies a Gateway-owned file path that does not exist on the Desktop host
- **THEN** Desktop resolves and reads that path through the active Gateway without requiring a local duplicate

### Requirement: Ambiguous preview targets remain local-first
Hermes Desktop SHALL preserve local-first resolution for transcript, tool, manual, picker, dropped-file, and other preview targets that are not explicitly Gateway-owned.

#### Scenario: Local attachment while connected remotely
- **WHEN** Desktop is connected to a remote Gateway and a preview target is an unclassified local attachment
- **THEN** Desktop attempts the existing local resolution path and does not send the target to the Gateway solely because remote mode is active

### Requirement: Cross-platform absolute paths are preserved
Preview normalization SHALL preserve POSIX absolute paths, Windows drive-absolute paths using either slash style, and UNC paths. It SHALL join the active working directory only to truly relative paths and SHALL retain Windows drive-relative semantics.

#### Scenario: Windows backslash drive path
- **WHEN** the target is `O:\workspace\a.md` and a working directory is present
- **THEN** normalization preserves `O:\workspace\a.md` without prefixing the working directory

#### Scenario: Windows forward-slash drive path
- **WHEN** the target is `O:/workspace/a.md` and a working directory is present
- **THEN** normalization preserves `O:/workspace/a.md` without prefixing the working directory

#### Scenario: POSIX absolute path
- **WHEN** the target is `/srv/workspace/a.md`
- **THEN** normalization preserves the path unchanged

#### Scenario: UNC path
- **WHEN** the target is `\\server\share\a.md`
- **THEN** normalization preserves the UNC path and does not treat it as workspace-relative

#### Scenario: Windows drive-relative path
- **WHEN** the target is `O:relative.md` and a working directory is present
- **THEN** normalization does not classify it as drive-absolute and applies the existing relative-path policy

### Requirement: Remote filesystem routing remains scoped
Every Gateway-owned preview read SHALL retain the active registered connection identifier and profile through the `hermes:api` boundary.

#### Scenario: Profile-scoped remote preview
- **WHEN** the user opens a Gateway-owned file while a registered remote connection and non-default profile are active
- **THEN** the filesystem request is sent to that connection and profile with the canonical listed path

### Requirement: Malformed URL-style Windows drive paths fail closed
On Windows, the Gateway SHALL reject exact leading-slash drive forms such as `/O:/workspace/a.md` with an explicit client error and SHALL NOT silently resolve them beneath the process working directory.

#### Scenario: Leading-slash drive path is rejected
- **WHEN** a filesystem endpoint receives `/O:/workspace/a.md` on Windows
- **THEN** it returns HTTP 400 with an invalid-path error before filesystem access

#### Scenario: Canonical drive path remains accepted
- **WHEN** a filesystem endpoint receives canonical `O:\workspace\a.md` or `O:/workspace/a.md` on Windows and the authorized regular file exists
- **THEN** it reads the intended file under the existing filesystem security checks

### Requirement: Cross-host regression verification uses remote-only fixtures
The change SHALL include automated verification in which the preview fixture exists on the Gateway host and not on the Desktop host.

#### Scenario: Different-host Windows preview
- **WHEN** Hermes Desktop on a different host opens a remote-only Windows drive path from the Gateway Files tree
- **THEN** the preview succeeds, the request path matches the listed path, and the rendered content checksum matches the Gateway fixture

#### Scenario: Existing POSIX behavior remains valid
- **WHEN** Desktop opens a remote-only POSIX path from a Linux or macOS Gateway
- **THEN** the existing remote preview behavior succeeds without path conversion regression
