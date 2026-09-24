# Proposal: Windows Update Venv Health & ACP Entry Point Repair

## Why

During `hermes update` on Windows hosts:
1. Interrupted updates, AV scans, or Windows file locking can leave half-deleted `*.dist-info` directories in the virtualenv's `site-packages` that lack a `METADATA` file. This causes `uv pip install` to abort during environment inspection before dependencies can be reconciled.
2. When the `hermes-acp` console script entry point is present in the `Scripts` directory (e.g. from past installations or sidecar setups), a venv update or repair might reinstall dependencies without the `[acp]` extra, leaving `hermes-acp.exe` broken upon execution.

## What Changes

- Add `_sanitize_corrupted_dist_info_directories` in `hermes_cli/update_cmd_deps.py` to detect and remove broken `*.dist-info` folders lacking `METADATA`, forcing dependency synchronization when corruption is healed.
- Add entry point verification and auto-repair in `hermes_cli/main_install_repair.py`: if `hermes-acp` exists, verify `import acp` succeeds, repairing via `pip install -e .[acp]` if missing.
- Clean up module-level imports in `hermes_cli/plugins.py`.
- Bump dependencies for security compliance: `anyio>=4.14.2,<5` (addressing GHSA-82r6-8w77-94w6) and `vitest: 4.1.11` in web dashboard.
- Add unit test coverage in `tests/hermes_cli/test_update_venv_health.py`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
None. Extends existing update resilience contracts.
