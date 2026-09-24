# Tasks: Windows Update Venv Health & ACP Repair

## 1. Implementation
- [x] 1.1 Implement `_sanitize_corrupted_dist_info_directories` in `hermes_cli/update_cmd_deps.py`
- [x] 1.2 Wire sanitization into `_sync_python_dependencies_after_pull` and force `deps_current = False` when cleaned
- [x] 1.3 Add `acp` import check and repair step in `hermes_cli/main_install_repair.py`
- [x] 1.4 Clean up `hermes_cli/plugins.py` import ordering
- [x] 1.5 Update dependencies in `pyproject.toml`, `uv.lock`, `web/package.json`, and `package-lock.json`

## 2. Unit Testing
- [x] 2.1 Add `test_sanitize_corrupted_dist_info_directories` to `tests/hermes_cli/test_update_venv_health.py`
- [x] 2.2 Verify `pytest tests/hermes_cli/test_update_venv_health.py` passes 100%

## 3. Compliance
- [x] 3.1 Validate change with `openspec validate fix-windows-update-venv-health --strict`
