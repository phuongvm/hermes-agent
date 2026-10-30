# Tasks: Timestamped Hermes Desktop Installer Filename

## 1. Python Publisher Timestamped Artifact Scoping (Seam A)

- [x] 1.1 Add `build_stamp: str | None = None` and CLI `--build-stamp` to `O:\workspaces\_config\agent4070\hermes\services\synology_release_publisher.py` with strict `YYYY-MM-DD-HHmm` calendar+24h validation, stamped artifact scoping (`Hermes-<Version>-win-x64-<BuildStamp>.exe` + `.blockmap`), and stamped `latest.yml` validation, with `--build-stamp` remaining OPTIONAL (default `None`, undated publication unchanged when omitted, covered by a CLI regression test), covered by Seam A unit tests in `O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py`.
  - **Proof**: `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -k "not SeamB" -q` (the selector MUST be `not SeamB`: the Seam B classes are `TestSeamBBuildScript` / `TestSeamBVaultwarden`, so `not seam_b` still selects them; verify with `--collect-only -q -k "not SeamB"` that no `TestSeamB*` node ID is listed)

## 2. PowerShell Build & Push Trigger-Timestamp Stamping (Seam B)

- [x] 2.1 Enhance `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` with `-BuildStamp` (default local `yyyy-MM-dd-HHmm` captured once at startup, strict validation), post-build stamped staging (`Hermes-<Version>-win-x64-<BuildStamp>.exe`, `.blockmap`, and active-version `latest.yml` rewrite), `-PushOnly` latest-VALID-stamped selection (stamps failing calendar/24h validation are ignored) + undated-to-stamped fallback, deterministic companion staging (stamped `.blockmap` + active-version `latest.yml` rewrite staged whenever the undated companions exist, independent of `-IncludeUpdateFeed`, so a later `-PushOnly -IncludeUpdateFeed` works), and `--build-stamp` forwarding, updating and expanding Seam B process tests (including the two-command `-PushOnly` then `-PushOnly -IncludeUpdateFeed` sequence and a malformed-plus-valid stamped candidate set) in `O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py`.
  - **Proof**: `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -q`

## 3. Independent Verification & Zero-Diff Gate

- [x] 3.1 Independently verify full Seam A + Seam B + Vaultwarden test suite (`rc=0`), strict OpenSpec validation (`rc=0`), and zero git diff under `O:\workspaces\oss\hermes-agent\apps\desktop\`.
  - **Proof**: `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest O:/workspaces/_config/agent4070/hermes/plugins/vaultwarden/test_vaultwarden.py O:/workspaces/_config/agent4070/hermes/services/tests/test_synology_release_publisher.py -q && git -C O:/workspaces/oss/hermes-agent diff --exit-code -- apps/desktop`
