## Purpose
Defines security, filtering, and safe resolution boundaries for the Hermes Gateway remote filesystem APIs (`/api/fs/list`, `/api/fs/read-text`, `/api/fs/write-text`, `/api/fs/read-data-url`).

## Requirements

### Requirement: Configurable Hidden Directory Filtering
The Gateway SHALL filter hidden directory entries from `GET /api/fs/list` by combining built-in defaults (`_DEFAULT_FS_READDIR_HIDDEN`) and user-configured directory names from `config.yaml` (`fs.hidden_dirs`).

#### Scenario: Default hidden directories are omitted
- **WHEN** a directory contains `.git`, `.venv`, or `node_modules`
- **THEN** `GET /api/fs/list` returns the directory listing with `.git`, `.venv`, and `node_modules` excluded.

#### Scenario: User-configured hidden directories are omitted
- **WHEN** `config.yaml` specifies `fs.hidden_dirs: ["_config", "$RECYCLE.BIN"]` and a directory contains `_config`, `$RECYCLE.BIN`, and `src`
- **THEN** `GET /api/fs/list` returns only `src` in `entries` and excludes `_config` and `$RECYCLE.BIN`.

### Requirement: Direct Access to Hidden Directories Fails Closed
The Gateway SHALL reject direct read, list, or download attempts for any path that traverses or resides within a hidden directory with an explicit `HTTP 403 Forbidden` response.

#### Scenario: Direct listing of hidden directory is rejected
- **WHEN** a client calls `GET /api/fs/list?path=O:/workspaces/_config` and `_config` is in `fs.hidden_dirs`
- **THEN** the Gateway returns `HTTP 403 Forbidden` with detail `"Access to hidden path is forbidden"`.

#### Scenario: Direct file read inside hidden directory is rejected
- **WHEN** a client calls `GET /api/fs/read-text?path=O:/workspaces/_config/asus-vb/hermes/config.yaml` and `_config` is in `fs.hidden_dirs`
- **THEN** the Gateway returns `HTTP 403 Forbidden` with detail `"Access to hidden path is forbidden"`.

### Requirement: Windows Safe Root Path Normalization
On Windows platforms, when `GET /api/fs/list` receives root path `/` or `\\`, the Gateway SHALL resolve the target to the configured working directory (`_fs_default_cwd()`) instead of escaping to the Gateway process's physical drive root.

#### Scenario: Querying root `/` on Windows resolves to default CWD
- **WHEN** running on Windows with `terminal.cwd: C:\workspace-template` and the client calls `GET /api/fs/list?path=/`
- **THEN** the Gateway resolves the path to `C:\workspace-template` and returns its contents.
