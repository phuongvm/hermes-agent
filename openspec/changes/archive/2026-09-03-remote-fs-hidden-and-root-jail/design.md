# Design: Configurable Hidden Directories and Safe Path Resolution

## Architecture Overview

```
[Desktop UI: Remote Picker / Files Pane]
                   │
                   ▼ GET /api/fs/list?path=<target>  /  GET /api/fs/read-text?path=<target>
        [Hermes Gateway: web_server.py]
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
[_fs_path(path)]       [_get_fs_readdir_hidden()]
 - Resolves '/' to      - Hardcoded defaults (.git, node_modules, ...)
   _fs_default_cwd()    - Dynamic config from config.yaml (`fs.hidden_dirs`)
 - Normalizes path      - Union set: default | configured (lowercased)
       └───────────┬───────────┘
                   ▼
       [Path Traversal & Component Guard]
  Does any component match _get_fs_readdir_hidden()?
        ├── YES ──► Raise HTTPException(403, "Access to hidden path is forbidden")
        └── NO  ──► Proceed to scandir / read
                   │
                   ▼
     [Directory Scan & Filtering]
  Filters out any entry matching _get_fs_readdir_hidden()
                   │
                   ▼
 [Response: Sanitized JSON Entries]
```

## Detailed Component Specifications

### 1. Dynamic Hidden Directories Resolver (`_get_fs_readdir_hidden`)
- Rename static set `_FS_READDIR_HIDDEN` to `_DEFAULT_FS_READDIR_HIDDEN`.
- Implement `_get_fs_readdir_hidden() -> set[str]`:
  ```python
  def _get_fs_readdir_hidden() -> set[str]:
      cfg = load_config_readonly().get("fs") or {}
      configured_hidden = cfg.get("hidden_dirs") or []
      if isinstance(configured_hidden, str):
          configured_hidden = [configured_hidden]
      return _DEFAULT_FS_READDIR_HIDDEN | set(configured_hidden)
  ```

### 2. Path Validation & Direct Traversal Guard
- In `_is_hidden_path(path: Path) -> bool`:
  - Check if any component in `path.parts` matches any entry in `_get_fs_readdir_hidden()` (case-insensitive on Windows/macOS).
- In `_fs_path(raw_path: str)`:
  - If `os.name == "nt"` and `raw in {"/", "\\", "", "."}`:
    - Target resolves to `Path(_fs_default_cwd()).resolve()`.
  - Otherwise resolve candidate.
  - If `_is_hidden_path(resolved_path)`:
    - Raise `HTTPException(status_code=403, detail="Access to hidden path is forbidden")`.

### 3. Directory Listing Filtering (`fs_list`)
- In `fs_list(path: str)`:
  - Target resolved via `_fs_path(path)`.
  - During `os.scandir(target)`:
    - Compute active hidden set: `hidden = {h.lower() for h in _get_fs_readdir_hidden()}`.
    - If `entry.name.lower() in hidden`: `continue`.
  - Return sanitized sorted entries.

## Risk & Mitigation Analysis
| Risk | Severity | Mitigation |
|---|---|---|
| User locks themselves out of valid workspace subfolder named like a hidden dir | Low | Only explicit entries configured in `fs.hidden_dirs` are hidden. Configurable per host. |
| Case-sensitivity mismatches between Linux/Windows | Medium | Lowercase normalization applied on Windows/macOS. |
