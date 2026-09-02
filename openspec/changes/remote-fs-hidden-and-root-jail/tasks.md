# Tasks: Configurable Hidden Directories and Windows Root Jail

## Phase 1: Test-Driven Development (RED Suite)
- [x] **Task 1.1**: Add unit tests in `tests/hermes_cli/test_web_server_fs.py` testing:
  - Dynamic `fs.hidden_dirs` filtering in `fs_list`.
  - HTTP 403 rejection on direct hidden directory list and file read.
  - Windows safe `/` normalization to `_fs_default_cwd()`.

## Phase 2: Gateway Implementation (GREEN Suite)
- [x] **Task 2.1**: Refactor `hermes_cli/web_server.py`:
  - Introduce `_DEFAULT_FS_READDIR_HIDDEN` and `_get_fs_readdir_hidden()`.
  - Implement `_is_hidden_path(path: Path) -> bool` with case-insensitive matching.
  - Update `_fs_path(raw_path: str)` to normalize Windows `/` to `_fs_default_cwd()` and reject hidden paths with HTTP 403.
  - Update `fs_list()` to use `_get_fs_readdir_hidden()`.
- [x] **Task 2.2**: Run pytest on `tests/hermes_cli/test_web_server_fs.py` and ensure 100% tests pass.

## Phase 3: Configuration & Live Gateway Verification
- [x] **Task 3.1**: Update `O:\workspaces\_config\asus-vb\hermes\config.yaml` and all profile configs with:
  ```yaml
  fs:
    hidden_dirs:
      - _config
      - System Volume Information
      - $RECYCLE.BIN
  ```
- [x] **Task 3.2**: Run E2E empirical verification across loopback testclient to confirm `_config` is completely hidden and direct access returns HTTP 403.
