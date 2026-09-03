# OpenSpec Exploration: Remote Filesystem Hidden Directories & Windows Root Jail

**Date**: 2026-09-02  
**Status**: Exploration / Incident Analysis  
**Project**: `hermes-agent` (`C:\workspace-template\hermes-agent`)  
**Scope**: Hermes Gateway Remote Filesystem API (`hermes_cli/web_server.py`) & Desktop Remote Picker Interaction  

---

## 1. Executive Summary & Problem Context

When connecting to a remote Hermes Gateway via Hermes Desktop or the Web Dashboard:
1. **Windows Drive Root Traversal (`Path('/')` Escaping)**:
   - When a user in the Remote Folder Picker clicks the root breadcrumb `/` or navigates upwards via `..`, the client issues `GET /api/fs/list?path=/`.
   - On Windows, `pathlib.Path('/')` is treated as non-absolute (`is_absolute() == False`).
   - The Gateway's `_fs_path('/')` resolves this relative to the Gateway process's current working directory (`Path.cwd()`), which resides on `O:\workspaces\oss\hermes-agent`.
   - As a result, `_fs_path('/')` resolves to the drive root `O:\`, exposing physical server storage.

2. **Exposure of Sensitive Host Configurations (`_config/`)**:
   - From `O:\`, navigating into `workspaces/` exposes `_config/`, which stores runtime databases (`state.db`, `kanban.db`), active session transcripts, credentials, and configuration files for all host machines (`asus-vb`, `agent4070`, `intel-nuc`, `hp-15s`).
   - The Gateway's `_FS_READDIR_HIDDEN` set in `hermes_cli/web_server.py` is hardcoded in Python with static entries (`.git`, `.venv`, `node_modules`, etc.), lacking any configuration mechanism in `config.yaml` to dynamically hide custom/host-specific folders.

---

## 2. Causal Chain & Faulty Boundaries

```
[Desktop Client UI: Remote Picker]
        │
        │ 1. User clicks root '/' or enters 'O:\workspaces'
        ▼
[GET /api/fs/list?path=/]
        │
        ▼
[hermes_cli/web_server.py: _fs_path]
        │
        ├─► FAULT 1: Path.is_absolute('/') is False on Windows
        │            candidate = Path.cwd() / Path('/')  --> Resolves to 'O:\' (Physical Drive Root)
        ▼
[hermes_cli/web_server.py: fs_list]
        │
        ├─► FAULT 2: _FS_READDIR_HIDDEN is a static hardcoded set in Python:
        │            {".git", ".venv", "node_modules", ...}
        │            '_config', '$RECYCLE.BIN', and 'System Volume Information' are not hidden.
        ▼
[JSON Response: { entries: [..., {"name": "_config", "path": "O:\\workspaces\\_config"}] }]
        │
        ▼
[CRITICAL IMPACT]: Full multi-host configuration and secret topology exposed to remote clients.
```

---

## 3. Solution Direction & Architectural Design

To resolve this without breaking existing platform contracts or hardcoding arbitrary paths, we establish a two-tiered architectural fix:

### Tier 1: Dynamic Configuration Merge (`fs.hidden_dirs`)
* Preserve the built-in system exclusion set as `_DEFAULT_FS_READDIR_HIDDEN`.
* Enhance `hermes_cli/web_server.py` to read `fs.hidden_dirs` from `config.yaml` via `load_config_readonly()`.
* Merge both collections:
  ```python
  def _get_fs_readdir_hidden() -> set[str]:
      cfg = load_config_readonly().get("fs") or {}
      configured_hidden = cfg.get("hidden_dirs") or []
      if isinstance(configured_hidden, str):
          configured_hidden = [configured_hidden]
      return _DEFAULT_FS_READDIR_HIDDEN | set(configured_hidden)
  ```
* Support case-insensitive comparison on Windows/macOS filesystems.

### Tier 2: Safe Root Jail & Traversal Guard
* In `_fs_path(raw_path)`:
  - If on Windows (`os.name == "nt"`) and `raw_path` is `/` or `\\`:
    - Map `/` to `_fs_default_cwd()` (`terminal.cwd` or configured workspace anchor) instead of escaping to `O:\`.
* If a client attempts direct access (e.g. `GET /api/fs/list?path=O:/workspaces/_config` or `GET /api/fs/read-text?path=.../_config/...`):
  - Check path components against `_get_fs_readdir_hidden()`.
  - Reject with **HTTP 403 Forbidden** (`detail="Access to hidden path is forbidden"`).

---

## 4. Verification Design & Test Strategy

### Automated Unit Tests (`tests/hermes_cli/test_web_server_fs.py`)
1. **Dynamic Hidden Filtering Test**:
   - Mock `config.yaml` with `fs.hidden_dirs = ["_config", "custom_secret_dir"]`.
   - Call `/api/fs/list` on a directory containing `_config`, `node_modules`, and `src`.
   - Verify that only `src` is returned in `entries`.
2. **Direct Access Rejection Test (HTTP 403)**:
   - Call `/api/fs/list?path=<hidden_dir>` and `/api/fs/read-text?path=<hidden_dir>/file.txt`.
   - Assert HTTP 403 Forbidden status code.
3. **Windows Safe Root Resolution Test**:
   - On Windows, call `/api/fs/list?path=/` and assert the returned path is anchored at `_fs_default_cwd()`.

### Manual E2E Verification
1. Configure `O:\workspaces\_config\asus-vb\hermes\config.yaml` with:
   ```yaml
   fs:
     hidden_dirs:
       - _config
       - System Volume Information
       - $RECYCLE.BIN
   ```
2. Restart Gateway and test `GET /api/fs/list?path=/` and `GET /api/fs/list?path=O:/workspaces` over loopback port 9119.
3. Verify in Hermes Desktop Remote Folder Picker that `_config` is completely omitted.
