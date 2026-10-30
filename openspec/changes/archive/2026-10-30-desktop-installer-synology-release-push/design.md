# Design: Hermes Desktop Installer Auto-Push to Synology Releases

## Context

`O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` builds the Windows NSIS installer from `O:\workspaces\oss\hermes-agent` into isolated staging directory `apps/desktop/release/win-nsis-build` and copies artifacts to `apps/desktop/release`. See `proposal.md` for motivation and `specs/desktop-release-publishing/spec.md` for behavioral contracts.

Live environment constraints established during exploration (`openspec/workspace/explorations/2026-10-29-desktop-installer-synology-release-push-exploration.md`):
- **Payload vs. Cloudflare Cap**: `Hermes-<Version>-win-x64.exe` is ~124 MB, exceeding Cloudflare Tunnel's 100 MB HTTP request-body limit on `https://dsm16.ptdev.vip` (`HTTP 413`). Direct WireGuard DSM WebAPI endpoints `https://10.8.0.60:5001` and `http://10.8.0.60:5000` are reachable (`85ms` RTT, `SYNO.API.Info` HTTP 200) and have no body-size cap.
- **Transport Availability**: TCP `445` (SMB) and `22` (SSH) are filtered on `10.8.0.60`; DSM WebAPI (`entry.cgi` / `query.cgi`) is the open transport.
- **FileStation API Constraints**: `SYNO.FileStation.Upload` (v2/v3) requires shared-folder paths (`/_Archives/...`, rejecting `/volume1/` with error `408`) and requires all form fields (`api`, `version`, `method`, `path`, `create_parents`, `overwrite`) to appear before the `file` binary stream in `multipart/form-data` (otherwise failing with error `401`).

## Goals / Non-Goals

**Goals:**
- Encapsulate all Synology DSM protocol quirks behind a deep, zero-external-dependency Python stdlib module (`synology_release_publisher.py`) invoked through a minimal CLI seam from `build-desktop-installer.ps1`.
- Stream 100+ MB binaries in bounded memory (8 MB chunks) without loading full installers into RAM.
- Guarantee zero edits to `O:\workspaces\oss\hermes-agent\apps\desktop\*` so `installer-version-integrity` and `upstream-sync-preservation` remain untouched.
- Verify behavior deterministically through two public testing seams against a real loopback Fake DSM HTTP server.

**Non-Goals:**
- Modifying Electron auto-update client channels, Cloudflare R2 feeds, or GitHub Actions release pipelines.
- Automating browser-based Cloudflare/SSO login flows.
- Pruning historical `.exe` files on Synology NAS.

## Decisions

### D1: Deep Python Stdlib Publisher Module + Thin PowerShell Orchestration Seam
- **Choice**: Implement DSM protocol logic in `O:\workspaces\_config\agent4070\hermes\services\synology_release_publisher.py` (Python 3.14 stdlib: `http.client`, `ssl`, `urllib.parse`, `json`, `uuid`, `pathlib`) and keep `build-desktop-installer.ps1` as a thin orchestrator (~35 lines added).
- **Alternatives Considered**:
  - *Inline PowerShell `Invoke-RestMethod -Form`*: Hashtable key ordering is not guaranteed to keep `file` last, `System.Net.Http` custom TLS callbacks in PowerShell are verbose, and host `Pester` is legacy v3.4.0 without clean in-process mock HTTP server fixtures.
  - *External `requests` / `httpx` dependency*: Introduces virtualenv coupling and buffers multipart payloads in memory unless `requests-toolbelt` is installed.
- **Rationale**: `http.client` supports exact multipart preamble writing followed by 8 MB file-chunk streaming over TLS (with scoped unverified SSL context only for private WireGuard IP `https://10.8.0.60:5001` whose certificate CN is `dsm16.ptdev.vip`) and integrates natively with `pytest` (`9.1.1`).

### D2: Observable Endpoint Ladder
- **Choice**: Candidate order when `SYNOLOGY_DSM_URL` is unset:
  1. `https://10.8.0.60:5001` (WireGuard HTTPS — encrypted, no 100 MB cap)
  2. `http://10.8.0.60:5000` (WireGuard HTTP — WireGuard tunnel is already encrypted at Layer 3)
  3. `https://dsm16.ptdev.vip` (Cloudflare Tunnel HTTPS — emits explicit size warning if file `> 95 MB`)
- **Test Seam**: The ordered candidate list is a public parameter of `publish_release_artifacts(candidates=...)` / CLI `--candidates url1,url2`. It defaults to the three production endpoints (exposed via `endpoint_ladder()`), so Seam A can place a refused-connection loopback port ahead of the Fake DSM to prove first-rung selection and ordered fallback without private monkeypatching. `--dsm-url` / `SYNOLOGY_DSM_URL` (argument wins; process environment only, never `.env`) replaces the ladder with a single fail-closed URL.
- **Probe Rule**: Query `/webapi/query.cgi?api=SYNO.API.Info&version=1&method=query&query=SYNO.API.Auth,SYNO.FileStation.Upload,SYNO.FileStation.List` with a 5s connect/read timeout; select the first candidate returning `{"success": true}` and use the advertised CGI paths and version bounds dynamically.

### D3: Streaming Multipart Wire Contract (`SYNO.FileStation.Upload` v2)
- **Choice**: Construct a deterministic `multipart/form-data; boundary=<uuid>` body with exact `Content-Length` computed upfront (`len(preamble_bytes) + file_size + len(epilogue_bytes)`):
  1. Form fields in order: `api=SYNO.FileStation.Upload`, `version=2`, `method=upload`, `path=/_Archives/inspire-robotics/project_files/Releases`, `create_parents=true`, `overwrite=true`.
  2. File part header: `Content-Disposition: form-data; name="file"; filename="<name>"`, `Content-Type: application/octet-stream`.
  3. Stream local file in `8 * 1024 * 1024` byte chunks directly to `conn.send(...)`.
  4. Epilogue boundary `\r\n--<boundary>--\r\n`.
  5. Authenticate the upload request with `_sid=<sid>` query parameter and `X-SYNO-TOKEN: <synotoken>` header (plus `Cookie: id=<sid>`).
  6. Login passes `enable_syno_token=yes` (POST body, so the password never appears in a URL); a login response without `synotoken` is a fail-closed error (logout still runs). Every `SYNO.FileStation.Upload`/`List` request carries `_sid`, cookie and `X-SYNO-TOKEN`.
- **Update-feed guard**: before any network call, `latest.yml` is parsed (stdlib regex) and must declare `version == <active version>` and only reference `Hermes-<V>-win-x64.exe[.blockmap]`; every published file name must be an active-version name.
- **Rationale**: Exact `Content-Length` + ordered preamble satisfies Synology nginx/CGI parsers without chunked-transfer incompatibilities and bounds RAM usage to ~8 MB regardless of installer size.

### D4: Public Testing Seams (`codebase-design` + `tdd`)
1. **Seam A (`publish_release_artifacts` & `synology_release_publisher.py` CLI)**:
   - Tested in `O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py` against a real `http.server.ThreadingHTTPServer` Fake DSM appliance bound to `127.0.0.1:0`.
   - Fake DSM strictly enforces real Synology invariants: rejects `/volume1/...` paths with error `408`, rejects multipart bodies where `file` appears before `path`/`overwrite` with error `401`, rejects missing/mismatched `_sid` or `X-SYNO-TOKEN` (error `119`), records `logout` calls, and serves `SYNO.FileStation.List` metadata for post-upload byte-size checks.
2. **Seam B (`build-desktop-installer.ps1` CLI Process Seam)**:
   - Tested by invoking `pwsh -NoProfile -File build-desktop-installer.ps1` (public flags: `-SkipPush`, `-PushOnly`, `-IncludeUpdateFeed`, `-SynologyDestPath`, `-SynologyDsmUrl`) with `-PushOnly` and `-SkipPush` against a temporary staged `TargetRoot` containing `install-stamp.json` (`baseVersion: "0.22.4"`), a current `Hermes-0.22.4-win-x64.exe`, and a stale `Hermes-0.22.2-win-x64.exe`.
   - Asserts exact version selection, `-IncludeUpdateFeed` trio upload, `-SkipPush` zero-network execution, and non-zero exit code propagation on DSM failure.

## Risks / Trade-offs

- **[Risk] DSM account has 2FA/OTP enforced** → **Mitigation**: Require a dedicated non-2FA DSM service account scoped to `/_Archives/inspire-robotics/project_files/Releases` in `.env` (`SYNOLOGY_DSM_USER` / `SYNOLOGY_DSM_PASS`). No OTP surface is provided (no unattended OTP provider exists).
- **[Risk] WireGuard tunnel down while building a >100 MB installer** → **Mitigation**: Endpoint ladder logs a clear warning when falling back to `https://dsm16.ptdev.vip` for files `> 95 MB` and surfaces actionable remediation (`check WireGuard tunnel to 10.8.0.60`) if `HTTP 413` occurs.
- **[Risk] Stale artifacts in `apps/desktop/release/win-nsis-build`** → **Mitigation**: Replace wildcard `Hermes-*.exe*` copy/selection in `build-desktop-installer.ps1` with version-scoped `Hermes-$InstallerVersion-*.exe*` selection so stale historical builds are never re-synced or uploaded.

## Migration Plan

1. Operator adds `SYNOLOGY_DSM_USER=<account>` and `SYNOLOGY_DSM_PASS=<secret>` to `O:\workspaces\_config\agent4070\hermes\.env` (zero changes required to `config.yaml`).
2. Deploy `synology_release_publisher.py`, updated `build-desktop-installer.ps1`, and `tests/test_synology_release_publisher.py`.
3. Smoke-verify live upload immediately using `.\build-desktop-installer.ps1 -PushOnly` with the existing `Hermes-0.22.4-win-x64.exe` before running a full rebuild.
4. **Rollback**: Pass `-SkipPush` at runtime or revert `build-desktop-installer.ps1`; local NSIS packaging remains completely unaffected.
