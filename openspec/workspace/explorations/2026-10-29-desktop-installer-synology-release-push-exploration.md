# Exploration: Hermes Desktop Installer Auto-Push to Synology FileStation Releases

> **Date**: 2026-10-29
> **Status**: Finalized (Frontier Settled → Proposed in `openspec/changes/desktop-installer-synology-release-push`)
> **Project**: `hermes-agent` (`O:/workspaces/oss/hermes-agent`)
> **Target Script**: `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`
> **Target Destination**: Synology DSM (`dsm16`) — `/volume1/_Archives/inspire-robotics/project_files/Releases` (FileStation path: `/_Archives/inspire-robotics/project_files/Releases`)

---

## 1. Problem Statement & Solution (`to-spec`)

### Problem Statement
When building a new Hermes Desktop Windows NSIS installer via `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1`, the operator currently obtains local artifacts under `apps/desktop/release/` (`Hermes-<version>-win-x64.exe`, `.blockmap`, `latest.yml`) and must manually open a browser session to `https://dsm16.ptdev.vip`, authenticate through SSO, navigate FileStation to `/_Archives/inspire-robotics/project_files/Releases`, and manually upload a ~124 MB installer executable. Manual upload is tedious, risks uploading stale cached builds from `win-nsis-build/`, and hits Cloudflare's 100 MB request-body cap if automated naively over the public tunnel.

### Solution
Extend `build-desktop-installer.ps1` with an automated, fail-closed post-build release publishing stage backed by a deep Python stdlib Synology FileStation publisher module (`synology_release_publisher.py`). Whenever packaging succeeds (or when `-PushOnly` is invoked), the script selects the exact current `$InstallerVersion` executable (`Hermes-$InstallerVersion-win-x64.exe`, plus optional `.blockmap` and `latest.yml` under `-IncludeUpdateFeed`), routes through an observable Endpoint Ladder prioritizing direct WireGuard DSM WebAPI (`https://10.8.0.60:5001` → `http://10.8.0.60:5000` → `https://dsm16.ptdev.vip`), authenticates via `SYNO.API.Auth`, normalizes `/volume1/_Archives/...` to `/_Archives/...`, streams the upload in 8 MB chunks with `overwrite=true`, verifies exact remote byte size via `SYNO.FileStation.List`, and deterministically logs out the DSM session.

---

## 2. Empirical Environment & Codebase Findings

### 2.1 Current Build Script (`build-desktop-installer.ps1`)
- **Location**: `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` (159 lines, PowerShell 7).
- **Version Resolution**: Resolves `$InstallerVersion` dynamically via `.venv\Scripts\python.exe` (`hermes_cli.version_info.get_version_info().base_version`), fallback `install-stamp.json`, or explicit `-Version` / `-c.extraMetadata.version=...`.
- **Isolated Packaging Output**: Runs `npm.cmd --prefix apps/desktop run builder -- --win nsis -c.directories.output=release/win-nsis-build -c.extraMetadata.version=$InstallerVersion`.
- **Produced Artifacts in `apps/desktop/release/win-nsis-build`**:
  - `Hermes-<Version>-win-x64.exe` (~124 MB, e.g., `Hermes-0.22.4-win-x64.exe` = 124 MB)
  - `Hermes-<Version>-win-x64.exe.blockmap` (~132 KB)
  - `latest.yml` (~219 B)
- **Current Sync Behavior**: Copies `Hermes-*.exe*` and `latest.yml` from `apps/desktop/release/win-nsis-build` to `apps/desktop/release`.
- **Artifact Selection Hazard**: `win-nsis-build` retains historical builds (`Hermes-0.22.2-win-x64.exe`, `Hermes-0.22.4-win-x64.exe`). Pushing `Hermes-*.exe` wildcard would re-upload stale builds unless scoped strictly to the newly built `$InstallerVersion` (`Hermes-$InstallerVersion-win-x64.exe`).

### 2.2 Synology (`dsm16`) Connectivity & Transport Probe
Live probes executed from `agent4070` against `dsm16`:

| Endpoint / Protocol | Probe Result | Implications |
| :--- | :--- | :--- |
| `https://dsm16.ptdev.vip/webapi/query.cgi` | HTTP 200 (`SYNO.API.Info` returns `SYNO.API.Auth` v1–7, `SYNO.FileStation.Upload` v2–3, `SYNO.FileStation.List` v1–2, `SYNO.FileStation.MD5` v1–2) | Reachable publicly via Cloudflare Tunnel (`Tunnel D`), **BUT** single-POST uploads of a **124 MB** `.exe` hit **Cloudflare's 100 MB HTTP body limit (`HTTP 413 Payload Too Large`)**. |
| `http://10.8.0.60:5000` & `https://10.8.0.60:5001` (WireGuard VPN) | `ping 10.8.0.60` → 85ms RTT (0% loss); TCP `5000`/`5001` open (`rc=0`); `SYNO.API.Info` returns `{"success":true}` | **Direct DSM WebAPI over WireGuard**. Completely bypasses Cloudflare's 100 MB body cap while using the exact same `SYNO.FileStation.Upload` API. |
| `10.8.0.60:445` (SMB) & `10.8.0.60:22` (SSH) | TCP connect `10035` (filtered/closed on WG interface) | SMB mount and SCP/SFTP are not exposed over the `10.8.0.60` container/WG path; DSM WebAPI (`5000`/`5001`) is the native open transport. |
| `SynoToken` in browser URL (`<REDACTED>`) | Ephemeral CSRF token tied to interactive browser cookie (`id=...`); `@url` probe returned DSM SSO login page | Cannot hardcode a browser `SynoToken` for unattended CLI builds. Automated builds require `SYNO.API.Auth` (`method=login`, `session=FileStation`) using service credentials stored in `.env`. |

### 2.3 Synology Path Semantics (`/volume1/...` vs `/_Archives/...`)
- **Linux / Volume path**: `/volume1/_Archives/inspire-robotics/project_files/Releases`
- **FileStation API `path` parameter**: `/_Archives/inspire-robotics/project_files/Releases` (as confirmed by the URL query parameter `openfile=%2F_Archives%2Finspire-robotics%2Fproject_files%2FReleases%2F`).
- **Critical API Quirks**:
  1. `SYNO.FileStation.Upload` rejects `/volume1/` prefixes with error `408` (No such file or directory) because FileStation paths begin at the shared folder root (`/_Archives/...`). The uploader module must normalize `/volume1/_Archives/...` → `/_Archives/...` automatically.
  2. In `SYNO.FileStation.Upload` (v2/v3) `multipart/form-data` requests, all metadata fields (`api`, `version`, `method`, `path`, `create_parents`, `overwrite`) **MUST** appear before the binary `file` part in the multipart body, or DSM ignores the parameters and fails with error `401` (Unknown error of file operation).

---

## 3. Architecture & Deep-Module Design (`codebase-design`)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                       build-desktop-installer.ps1                            │
│                                                                              │
│  1. Resolve $InstallerVersion (hermes_cli.version_info / install-stamp.json) │
│  2. npm --prefix apps/desktop run build                                      │
│  3. electron-builder --win nsis -> release/win-nsis-build                    │
│  4. Sync current version artifacts -> apps/desktop/release                   │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼  (Seam: Publish-SynologyRelease)
┌──────────────────────────────────────────────────────────────────────────────┐
│  Deep Module: Synology FileStation Release Publisher                         │
│                                                                              │
│  Small Interface:                                                            │
│    Publish-SynologyRelease -Files <Path[]> [-RemoteDir <String>]             │
│                            [-SkipUpload]                                     │
│                                                                              │
│  Deep Implementation (Hidden Behind Seam):                                   │
│    ├─ Endpoint Ladder: probe https://10.8.0.60:5001 / http://10.8.0.60:5000  │
│    │  first (bypass CF 100MB cap), fallback to https://dsm16.ptdev.vip       │
│    ├─ Path Normalizer: /volume1/_Archives/... -> /_Archives/...              │
│    ├─ Auth Lifecycle: SYNO.API.Auth login (sid + SynoToken) + finally logout │
│    ├─ Multipart Stream Uploader: SYNO.FileStation.Upload v2 (streaming IO)   │
│    └─ Post-Upload Zero-Trust Verification: SYNO.FileStation.List size match  │
│       (+ optional SYNO.FileStation.MD5 / SHA-256 manifest verification)      │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Module Depth Analysis
- **Module**: Synology FileStation Release Publisher (invoked at the post-build stage of `build-desktop-installer.ps1`).
- **Interface**: Callers pass only the built file path(s), target folder path (`/volume1/_Archives/inspire-robotics/project_files/Releases`), and optional `-SkipPush` switch. Credentials and endpoint URLs load from environment/config (`SYNOLOGY_DSM_URL`, `SYNOLOGY_DSM_USER`, `SYNOLOGY_DSM_PASS`).
- **Depth & Leverage**: Hides Synology path normalization (`/volume1` stripping), WireGuard vs Cloudflare endpoint selection (avoiding HTTP 413 on >100 MB installers), DSM `SYNO.API.Auth` session/token negotiation, multipart field-ordering quirks, and remote byte-size verification behind a single call.
- **Test Surface (Seam)**: Testable against a mock HTTP handler or dry-run probe without running a full 5-minute `electron-builder` packaging pass (`-SkipBuild` / standalone publish function test).

---

## 4. Settled Frontier Decisions (`grilling` Round 1 — Approved by Commander)

| ID | Decision Topic | Approved Resolution |
| :--- | :--- | :--- |
| **Q1** | **Project Ownership (`OPENSPEC_ROOT`)** | Activated `hermes-agent` (`O:\workspaces\oss\hermes-agent\openspec`) as canonical owner. |
| **Q2** | **Endpoint Ladder (124 MB vs CF 100 MB Cap)** | Probe `SYNOLOGY_DSM_URL` override first (if set); otherwise ladder `https://10.8.0.60:5001` → `http://10.8.0.60:5000` → `https://dsm16.ptdev.vip` via `SYNO.API.Info`. |
| **Q3** | **DSM Unattended Auth & Session Hygiene** | Authenticate via `SYNO.API.Auth` (`session=FileStation`) reading `SYNOLOGY_DSM_USER` and `SYNOLOGY_DSM_PASS` (optional `SYNOLOGY_DSM_OTP`) from process env or `O:\workspaces\_config\agent4070\hermes\.env`. Always execute `method=logout` inside a `finally` block. |
| **Q4** | **Artifact Scope & Overwrite Policy** | Default upload targets exact current build `Hermes-$InstallerVersion-win-x64.exe` flat into `/_Archives/inspire-robotics/project_files/Releases` with `overwrite=true` (`create_parents=true`), plus optional `-IncludeUpdateFeed` for `.blockmap` and `latest.yml`. Verify exact remote byte size via `SYNO.FileStation.List`. |
| **Q5** | **Failure Mode & Developer Flags** | Fail-closed (`exit != 0` on auth, upload, or size-verification failure). Expose `-SkipPush` (local build only) and `-PushOnly` (publish existing built `$InstallerVersion` artifact from `release/` without rebuilding). |

---

## 5. Public Testing Seams (`tdd`) & Spec Catalog Cross-Reference

### Confirmed Public Testing Seams
1. **Seam A — `publish_release_artifacts()` / CLI vs. Loopback Fake DSM HTTP Server**: Exercises real socket I/O, endpoint ladder failover, `/volume1` path normalization, `SYNO.API.Auth` login + guaranteed `logout` on exception, strict metadata-before-file multipart ordering, 8 MB chunked streaming integrity, and `SYNO.FileStation.List` byte-size mismatch fail-closed rejection.
2. **Seam B — `build-desktop-installer.ps1` Process Seam (`-PushOnly` / `-SkipPush`)**: Verifies exact `$InstallerVersion` selection (ignoring stale `Hermes-0.22.2-win-x64.exe` in staging), `-SkipPush` network bypass, and non-zero exit propagation when publishing fails.

### Full Spec Catalog Cross-Reference (23 Canonical Specs Audited)
- **`installer-version-integrity`**: Preserved 100% — out-of-tree service module touches zero `apps/desktop/` files while consuming canonical `$InstallerVersion`.
- **`desktop-runtime-version`**: Aligned — version-exact upload prevents version drift between built installer and NAS release archive.
- **`fleet-hermes-upgrade-orchestration`**: Aligned — adopts strict fail-closed non-zero exit and diagnostic reporting on remote failure.
- **`upstream-sync-preservation` & remaining 19 specs**: Zero overlap and zero upstream merge conflict surface.

## 6. Out of Scope

- Modifying Electron auto-updater client code inside `apps/desktop/electron/` or changing Cloudflare R2 / GitHub Releases CI workflows.
- Interactive browser OAuth/SSO login automation.
- Deleting historical installer versions already stored in `/volume1/_Archives/inspire-robotics/project_files/Releases`.
