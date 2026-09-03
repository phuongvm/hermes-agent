# Proposal: Configurable Hidden Directories and Windows Root Jail in Remote Filesystem API

## Why
When connecting to a remote Hermes Gateway via Hermes Desktop or the Web Dashboard:
1. **Windows Drive Root Traversal (`Path('/')` Escaping)**:
   - When a user in the Remote Folder Picker clicks the root breadcrumb `/` or navigates upwards via `..`, the client issues `GET /api/fs/list?path=/`.
   - On Windows, `pathlib.Path('/')` is treated as non-absolute (`is_absolute() == False`).
   - The Gateway's `_fs_path('/')` resolves this relative to the Gateway process's current working directory (`Path.cwd()`), which resides on `O:\workspaces\oss\hermes-agent`.
   - As a result, `_fs_path('/')` resolves to the drive root `O:\`, exposing physical server storage and letting clients escape the intended workspace directory (`C:\workspace-template`).
2. **Exposure of Sensitive Host Configurations (`_config/`)**:
   - From `O:\`, navigating into `workspaces/` exposes `_config/`, which stores runtime databases (`state.db`, `kanban.db`), active session transcripts, credentials, and configuration files for all host machines (`asus-vb`, `agent4070`, `intel-nuc`, `hp-15s`).
   - The Gateway's `_FS_READDIR_HIDDEN` set in `hermes_cli/web_server.py` is hardcoded in Python with static entries (`.git`, `.venv`, `node_modules`, etc.), lacking any configuration mechanism in `config.yaml` to dynamically hide custom/host-specific folders.

## What Changes
1. **Dynamic Configuration Merge (`fs.hidden_dirs`)**:
   - Preserve default built-in exclusions (`_DEFAULT_FS_READDIR_HIDDEN`).
   - Read `fs.hidden_dirs` from `config.yaml` via `load_config_readonly()`.
   - Exclude all matching directories (case-insensitively on Windows/macOS) from `GET /api/fs/list`.
2. **Windows Safe Root Normalization (`_fs_path`)**:
   - On Windows, map root path `/` and `\\` to `_fs_default_cwd()` (`terminal.cwd: C:\workspace-template`) rather than resolving to the physical disk root of the Gateway process.
3. **Fail-Closed Direct Access Rejection (HTTP 403)**:
   - If a client attempts to list or read a path containing any directory matching the hidden set (e.g. `GET /api/fs/list?path=O:/workspaces/_config` or `GET /api/fs/read-text?path=.../_config/...`), return `HTTP 403 Forbidden` (`detail="Access to hidden path is forbidden"`).

## Capabilities Affected
- Remote Filesystem Navigation (`/api/fs/list`)
- File Preview & Read Text (`/api/fs/read-text`, `/api/fs/read-data-url`)
- Desktop Remote Folder Picker integration

## Impact & Compatibility
- **Backward Compatibility**: Fully compatible. Default behavior preserves existing hidden entries (`.git`, `node_modules`, `.venv`, etc.).
- **Security**: Hardens the Gateway against directory traversal and multi-host secret exposure.
