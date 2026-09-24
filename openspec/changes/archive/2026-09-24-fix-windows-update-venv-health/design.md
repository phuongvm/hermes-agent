# Design: Windows Update Venv Health & ACP Repair

## Context

On Windows, filesystem operations inside active Python virtualenvs can leave partial residue when an update process is terminated abruptly. `uv` strictly validates all `*.dist-info` directories in `site-packages`. When a folder like `broken_pkg-0.8.4.dist-info` exists without a `METADATA` file, `uv` raises an error and halts execution.

## Architecture & Decisions

### D1: Pre-Sync Dist-Info Sanitization
Before invoking dependency sync in `_sync_python_dependencies_after_pull`:
1. Resolve candidates across `Lib/site-packages` and `lib/python*/site-packages`.
2. Find any `*.dist-info` directory where `(item / "METADATA").is_file()` is false.
3. Remove using `shutil.rmtree(..., ignore_errors=True)`.
4. If any directory was cleaned, set `deps_current = False` to guarantee a fresh reinstall replaces the damaged package.

### D2: Entry Point Integrity Check for ACP
In `_verify_console_scripts_installed`:
1. Check whether `scripts_dir / "hermes-acp.exe"` or `hermes-acp` exists.
2. Probe `venv_python -c "import acp"`.
3. If non-zero, invoke `_run_repair_step` with `install -e .[acp]` in quarantined mode.
