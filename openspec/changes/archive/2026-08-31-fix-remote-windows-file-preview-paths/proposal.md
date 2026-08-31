## Why

Hermes Desktop corrupts Gateway-owned Windows paths when a file exists only on a remote Gateway: Electron rejects the path using the Desktop host's filesystem, the renderer misclassifies `O:\...` as relative, and the resulting `/api/fs/read-text` request returns `404 File not found`. The Files pane must preserve remote filesystem authority and cross-platform path semantics so remote preview does not depend on the same path existing locally.

## What Changes

- Add explicit filesystem authority to preview normalization so remote Files and Review tree entries are treated as Gateway-owned.
- Preserve Windows drive-absolute, UNC, and POSIX absolute paths; join `cwd` only to truly relative paths, including preserving Windows drive-relative semantics.
- Keep transcript, tool, manual, picker, dropped-file, and other ambiguous targets on the existing local-first path to prevent local-path disclosure to a remote Gateway.
- Preserve active `connectionId` and profile routing for remote filesystem reads.
- Harden Windows Gateway handling of exact URL-style drive paths (`/X:/...`) so they are narrowly canonicalized or explicitly rejected instead of silently retargeted under process CWD.
- Add unit, integration, and cross-host E2E coverage with fixtures that exist only on the Gateway host.

## Capabilities

### New Capabilities

- `desktop-remote-file-preview`: Defines authority-aware remote preview, cross-platform path preservation, remote routing, defensive Windows Gateway path handling, and cross-host verification behavior.

### Modified Capabilities

None.

## Impact

- Desktop renderer preview normalization in `apps/desktop/src/lib/local-preview.ts`.
- Gateway-owned Files and remote Review tree activation call sites.
- Preview and remote filesystem tests under `apps/desktop/src/`.
- Windows filesystem path handling and tests in `hermes_cli/web_server.py` and `tests/hermes_cli/test_web_server_files.py`.
- No new dependency, API family, or breaking public interface is required; the existing `/api/fs/*` transport and `hermes:api` routing remain in place.
