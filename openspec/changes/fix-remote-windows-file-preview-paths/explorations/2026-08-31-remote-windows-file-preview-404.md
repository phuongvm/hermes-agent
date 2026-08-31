# Remote Windows File Preview 404 Exploration

**Date:** 2026-08-31
**Mode:** `opsx-explore`
**Status:** Proposal Ready — root cause and permanent solution boundary established
**Target project:** `O:/workspaces/oss/hermes-agent`

## Problem Statement

Hermes Gateway and Dashboard run on a Windows host. A Hermes Desktop instance on another machine can connect to that gateway and display the remote workspace tree, but opening a file from the Files pane fails with a `404` surfaced through `hermes:api` (`File not found`). The same operation succeeds when Desktop runs on the gateway host.

The observed environment difference suggests a local/remote filesystem boundary defect. The leading hypothesis is Windows path canonicalization, but the exact production request payload has not yet been captured.

## Exploration Constraints

This investigation is read-only with respect to application code. It traces existing behavior, executes focused tests and causal experiments, and packages proposal-ready findings. It does not implement a fix.

## Current Architecture

```text
Windows Gateway
  GET /api/fs/list
       |
       | entry.path = str(target / entry.name)
       v
Desktop Files pane
  TreeNode.id = entry.path
       |
       | activate file
       v
normalizeOrLocalPreviewTarget(path, cwd)
       |
       v
PreviewTarget.path
       |
       v
filePathForTarget(target)
       |
       | target.path has precedence over target.url
       v
readDesktopFileText(path) / readDesktopFileDataUrl(path)
       |
       v
hermesApi({ connectionId, profile, path: /api/fs/... })
       |
       v
Gateway _fs_path(raw_path)
       |
       v
Windows filesystem
```

## Relevant Code Paths

### Gateway listing and reads

- `hermes_cli/web_server.py:2299-2316` — `_fs_path()`
- `hermes_cli/web_server.py:3041-3064` — `GET /api/fs/list`
- `hermes_cli/web_server.py:3067-3088` — `GET /api/fs/read-text`
- `hermes_cli/web_server.py:3138-3149` — `GET /api/fs/read-data-url`
- `hermes_cli/web_server.py:3152-3160` — `GET /api/fs/download`

`GET /api/fs/list` returns each entry using `str(target / entry.name)`. On Windows, the canonical path is expected to remain Windows-native, for example:

```text
O:\workspaces\oss\hermes-agent\README.md
```

### Desktop filesystem transport

- `apps/desktop/src/lib/desktop-fs.ts:44-51` — remote-mode and profile selection
- `apps/desktop/src/lib/desktop-fs.ts:65-66` — FS request construction
- `apps/desktop/src/lib/desktop-fs.ts:79-98` — remote directory and text reads
- `apps/desktop/src/lib/desktop-fs.ts:121-128` — remote data URL reads
- `apps/desktop/src/api/client.ts:73-99` — active connection scoping
- `apps/desktop/electron/main.ts:15910-16013` — `hermes:api` routing

The renderer URL-encodes the supplied path but does not intentionally convert a Windows drive path to POSIX form:

```text
O:\workspaces\project\file.txt
    -> /api/fs/read-text?path=O%3A%5Cworkspaces%5Cproject%5Cfile.txt
```

The request also carries the active `connectionId` and profile when present.

### Preview normalization

- `apps/desktop/src/app/contrib/panes.tsx:72-80` — Files pane activation
- `apps/desktop/src/lib/local-preview.ts:59-81` — path joining and file URL construction
- `apps/desktop/src/lib/local-preview.ts:181-220` — renderer fallback target
- `apps/desktop/src/lib/local-preview.ts:223-276` — remote enrichment and normalization ladder
- `apps/desktop/electron/main.ts:5894-5935` — Electron-local preview target probe
- `apps/desktop/src/app/chat/right-rail/preview-file.tsx:178-190` — preview file path selection
- `apps/desktop/src/app/chat/right-rail/preview-file.tsx:735-805` — preview file read

`filePathForTarget()` prefers `target.path`. It only derives a path from `target.url` when `target.path` is absent. Therefore, file URL parsing is not automatically part of the canonical path when the target retains its path field.

## Evidence

### E1 — File URL parsing creates a leading slash before a Windows drive

Command:

```bash
node -e "const u=new URL('file:///O:/workspaces/project/file.txt'); console.log(JSON.stringify({pathname:u.pathname,decoded:decodeURIComponent(u.pathname)}))"
```

Observed output:

```json
{"pathname":"/O:/workspaces/project/file.txt","decoded":"/O:/workspaces/project/file.txt"}
```

**Finding:** Any fallback that reconstructs a Windows filesystem path from `URL.pathname` can produce `/O:/...` rather than `O:/...`.

### E2 — Gateway resolves canonical Windows paths correctly

Command invoked `_fs_path()` directly on the Windows host with three input forms.

Observed output:

```json
{
  "O:\\workspaces\\oss\\hermes-agent\\README.md": "O:\\workspaces\\oss\\hermes-agent\\README.md",
  "O:/workspaces/oss/hermes-agent/README.md": "O:\\workspaces\\oss\\hermes-agent\\README.md",
  "/O:/workspaces/oss/hermes-agent/README.md": "O:\\workspaces\\oss\\hermes-agent\\workspaces\\oss\\hermes-agent\\README.md"
}
```

**Findings:**

1. Backslash Windows drive paths resolve correctly.
2. Forward-slash Windows drive paths resolve correctly.
3. A leading-slash drive path (`/O:/...`) resolves to the wrong location.

This is a confirmed normalization defect, independent of whether it is the production trigger.

### E3 — Focused Desktop tests pass but omit remote Windows drive cases

Command:

```bash
npm test -- --run src/lib/desktop-fs.test.ts src/lib/local-preview.test.ts src/app/right-sidebar/files/ipc.test.ts
```

Observed result:

```text
Test Files  3 passed (3)
Tests       36 passed (36)
Duration    4.66s
Exit code   0
```

The tests cover:

- local versus remote filesystem selection;
- connection and profile routing;
- POSIX remote paths;
- Windows paths in `.gitignore` tree filtering;
- UNC and ordinary file URL serialization.

They do not cover:

- `O:\...` or `O:/...` through the remote `desktop-fs` request builder;
- `/O:/...` at Gateway `_fs_path()`;
- the full `list entry -> preview target -> hermes:api` Windows-remote flow.

### E4 — Routing contracts are covered at unit level

Existing tests verify that registered remote and SSH filesystem requests carry the active `connectionId` and profile. The deterministic malformed-path reproduction produced the exact 404 on the intended Gateway without requiring a routing mismatch, so routing is rejected as the cause of this incident while remaining protected by the proposal regression contract.

### E5 — Exact Desktop path corruption is deterministic

The current exported production function was executed from the Desktop package with a Windows Gateway path and the active workspace CWD:

```text
raw = O:\workspaces\a.md
cwd = O:/workspaces
```

Observed target and request:

```json
{
  "path": "O:/workspaces/O:\\workspaces\\a.md",
  "endpoint": "/api/fs/read-text?path=O%3A%2Fworkspaces%2FO%3A%5Cworkspaces%5Ca.md"
}
```

Controls showed that POSIX absolute paths are preserved, real relative paths are joined correctly, and the Windows path is preserved when `cwd` is absent. The defect is the absolute-path predicate at `local-preview.ts:200`, which treats a drive-absolute path as relative whenever a CWD is supplied.

### E6 — Exact malformed target produces the observed Gateway 404

A real fixture was created at `O:\workspaces\.hermes-explore-remote-preview.txt`, read through the actual Gateway `_fs_path()` and `_fs_regular_file()` functions, then removed.

Canonical input succeeded:

```json
{
  "input": "O:\\workspaces\\.hermes-explore-remote-preview.txt",
  "exists": true,
  "status": 200,
  "size": 11
}
```

The Desktop-style joined input failed with the observed response:

```json
{
  "input": "O:/workspaces/O:\\workspaces\\.hermes-explore-remote-preview.txt",
  "resolved": "O:workspaces\\.hermes-explore-remote-preview.txt",
  "exists": false,
  "status": 404,
  "detail": "File not found"
}
```

This closes the executable causal chain from renderer fallback to Gateway 404. The separate `/O:/...` behavior remains a confirmed defensive-hardening gap, but it is not required to trigger this incident.

## Root Cause

The production root cause is a two-boundary Desktop defect:

1. `previewFile()` passes a Gateway-owned project-tree path and `$currentCwd` to `normalizeOrLocalPreviewTarget()`.
2. Electron `normalizePreviewTarget()` probes the Desktop client's local filesystem. For a remote-only file, `fileExists()` returns false and the normalizer returns `null`.
3. Renderer fallback `localPreviewTarget()` recognizes only leading `/` as absolute. It misclassifies `O:\...` as relative and prefixes the CWD.
4. `readDesktopFileText()` sends the malformed `PreviewTarget.path` to the intended remote Gateway at `/api/fs/read-text`.
5. Gateway resolves/stat-checks the wrong target and returns `404 {"detail":"File not found"}`.

This explains the observed asymmetry: a file duplicated locally bypasses the fallback, while a file existing only on the remote Gateway enters it and fails.

## Hypothesis Disposition

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Gateway cannot access drive `O:` | Rejected | Canonical `O:\...` fixture returned 200 |
| Gateway cannot process Windows paths generally | Rejected | Backslash and drive-forward-slash forms resolve correctly |
| Wrong profile/connection is required to reproduce the incident | Rejected for this incident | The malformed path reproduced the exact 404 on the intended Gateway; routing tests also pass |
| `/O:/...` is the primary incident trigger | Rejected as primary | The full failure occurs before URL-path reconstruction |
| Electron local existence probe plus Windows absolute-path misclassification | Confirmed root cause | Static trace, exact TypeScript execution, and actual Gateway 404 |
| Missing Windows remote-preview regression coverage | Confirmed contributing gap | Existing focused tests pass without this case |

## Permanent Solution Boundary

The primary correction belongs in Desktop because Desktop creates the invalid request. Gateway normalization is optional defense in depth, not the primary fix.

### Decision 1 — Explicit filesystem authority

`normalizeOrLocalPreviewTarget()` SHALL accept an explicit authority/provenance option. The project Files pane and remote Review tree SHALL mark their paths as Gateway-owned. Transcript/tool/manual preview targets SHALL retain the existing local-first behavior because their provenance can be local or ambiguous.

For a Gateway-owned target while the active connection is remote:

- do not use Electron-local existence as an admission decision;
- classify the path in the renderer;
- enrich/read it through the authenticated remote filesystem transport;
- preserve active `connectionId` and profile routing.

This avoids both the incident and the local-path exfiltration risk of globally bypassing Electron normalization in remote mode.

### Decision 2 — Cross-platform absolute-path classifier

Renderer fallback SHALL treat only these forms as absolute:

```text
/posix/path
X:\windows\path
X:/windows/path
\\server\share\path
```

It SHALL keep `X:relative.txt` relative because that is a Windows drive-relative form. `file:` URLs remain in their dedicated URL parsing branch. Only truly relative paths may be joined with `cwd`.

### Decision 3 — Narrow Gateway defense

On Windows, Gateway SHOULD either canonicalize exactly one leading slash from the syntactically exact form `/[A-Za-z]:[\\/]...` or reject it explicitly with `400`. It MUST NOT silently resolve it below the process CWD. This hardening protects other clients but does not replace the Desktop producer fix.

## Proposal-Ready Scope

### Proposed change name

`fix-remote-windows-file-preview-paths`

### Objective

Preserve Gateway-owned paths from Files/Review trees across remote preview construction and read them on the authoritative Gateway without local-host existence gating or cross-platform path corruption.

### In scope

1. Add explicit preview filesystem authority/provenance at normalization call sites.
2. Mark Files pane and remote Review tree paths as Gateway-owned.
3. Add a narrow cross-platform absolute-path classifier.
4. Preserve current local-first behavior for ambiguous transcript, tool, and manual preview targets.
5. Optionally harden Windows Gateway handling of exact `/X:/...` input.
6. Add unit, integration, and cross-host E2E regression coverage.

### Out of scope

1. Redesigning the Files pane or preview rail.
2. Replacing `/api/fs/*` with a virtual filesystem abstraction.
3. Relaxing sensitive-file restrictions.
4. Changing local reveal, rename, trash, picker, or dropped-file behavior.
5. Treating every target in remote mode as Gateway-owned.

## Requirements

### R1 — Gateway-owned path round-trip

A path returned by remote `GET /api/fs/list` SHALL reach the corresponding remote read endpoint unchanged.

### R2 — Authority-aware normalization

A Gateway-owned project-tree path SHALL NOT be rejected, rewritten, or classified by Electron-local file existence. Ambiguous targets SHALL preserve existing local-first semantics.

### R3 — Absolute path classification

The renderer SHALL preserve POSIX, Windows drive-absolute, and UNC paths, SHALL keep drive-relative paths relative, and SHALL join only truly relative paths with `cwd`.

### R4 — Routing preservation

Every remote filesystem request SHALL retain the active registry `connectionId` and profile through `hermes:api`.

### R5 — Defensive Gateway behavior

On Windows, exact `/X:/...` input SHALL be canonicalized narrowly or rejected explicitly; it SHALL never be silently retargeted under process CWD.

### R6 — Platform and security preservation

The change SHALL preserve Unicode and encoded spaces, sensitive-file guards, local Desktop behavior, and remote/local authority separation.

## Regression and E2E Contract

### Unit tests

1. `localPreviewTarget('O:\\workspace\\a.md', 'O:/workspace')` preserves the input path.
2. `O:/...`, `/srv/...`, and `\\\\server\\share\\...` remain absolute.
3. `O:relative.md`, `relative/a.md`, and `../a.md` remain relative and are joined according to existing semantics.
4. A Gateway-owned remote target bypasses Electron normalization; an ambiguous target does not.
5. Remote read retains `connectionId`, profile, endpoint, and exact encoded path.
6. Gateway exact `/X:/...` policy is covered explicitly.

### Integration test

Exercise:

```text
/api/fs/list Windows entry
→ Files pane activation
→ authority-aware preview normalization
→ /api/fs/read-text
→ canonical Gateway file
```

The fixture SHALL exist only in the Gateway filesystem and not on the Desktop host.

### Cross-host E2E

| Gateway | Desktop | Fixture | Expected |
|---|---|---|---|
| Windows | Different Windows host | Remote-only `O:\...` text file | Preview succeeds |
| Windows | Linux/macOS | Remote-only `O:\...` text file | Preview succeeds |
| Windows | Same host | `O:\...` file | Preview succeeds without relying on duplication |
| Linux/macOS | Any | Remote-only `/srv/...` file | Existing behavior preserved |
| Windows | Any | UNC file when environment supports it | Existing authorized behavior preserved |

Capture endpoint, decoded path category, connection ID, profile, HTTP status, and rendered content checksum. Do not log file content or secrets.

## Negative Impact Analysis

The three worst unintended effects and their controls are:

1. **Local-path exfiltration to a remote Gateway** — controlled by explicit Gateway authority at trusted project/review tree call sites, not a global remote-mode bypass.
2. **Drive-relative or POSIX path corruption** — controlled by a narrow classifier and matrix tests for `X:\`, `X:/`, `X:relative`, POSIX, UNC, and relative paths.
3. **Gateway retargeting/security regression** — controlled by exact-pattern normalization or explicit rejection before existing sensitive-file and regular-file checks.

## Hard Exit Gate

**Exit state:** Proposal Ready — causal reproduction, solution boundary, and verification contract complete.
**Next workflow:** `/opsx-propose fix-remote-windows-file-preview-paths`

The proposal SHALL preserve the root cause, authority boundary, requirements, and regression contract above. It SHALL NOT reduce the solution to a Gateway-only slash normalization patch.
