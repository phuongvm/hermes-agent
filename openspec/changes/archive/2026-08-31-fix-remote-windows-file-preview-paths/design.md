## Context

See `proposal.md` for motivation and `explorations/2026-08-31-remote-windows-file-preview-404.md` for causal evidence. Preview normalization currently combines two authorities: Electron probes the Desktop-local filesystem, while remote filesystem reads are served by the active Gateway. A Gateway-owned Windows path that is absent locally therefore falls into renderer fallback, where a leading-slash-only absolute-path check prefixes `cwd` and corrupts the request.

The same normalization function also serves transcript, tool, and manual targets whose filesystem authority is ambiguous. Any global remote-mode bypass would risk forwarding a local-only path to a remote Gateway.

## Goals / Non-Goals

**Goals:**

- Represent filesystem authority explicitly at the normalization boundary.
- Preserve remote Files and Review tree paths exactly through the authenticated Gateway read.
- Classify POSIX, Windows drive-absolute, drive-relative, and UNC paths without host-OS assumptions.
- Keep existing local-first behavior for targets without trusted Gateway provenance.
- Fail closed on malformed URL-style Windows drive paths at the Gateway.
- Make the failure reproducible at unit, integration, and cross-host E2E levels.

**Non-Goals:**

- Introduce a virtual filesystem abstraction or replace `/api/fs/*`.
- Route every preview target to the Gateway whenever Desktop is in remote mode.
- Change local picker, drop, reveal, rename, trash, or browser-open behavior.
- Relax sensitive-file, regular-file, authentication, connection, or profile controls.

## Decisions

### 1. Pass explicit authority into preview normalization

Add a narrow normalization option representing `local-first` versus `gateway` authority. Keep `local-first` as the default so existing callers remain unchanged. Files pane activation passes `gateway` only when the active filesystem mode is remote. The remote Review tree does the same for paths produced by Gateway review data.

For `gateway` authority in remote mode, renderer classification happens before any Electron-local existence probe. Remote enrichment then uses the existing `readDesktopFileText` or `readDesktopFileDataUrl` transport, which already carries active connection and profile scope.

**Why:** Authority is known at the trusted tree call site, not inferable from the path string or global connection mode.

**Alternatives rejected:**

- **Skip Electron normalization for every target in remote mode:** can disclose local-only transcript, picker, or dropped-file paths to the remote Gateway.
- **Add authority to persisted `PreviewTarget`:** unnecessary; authority is needed while constructing the target, while the completed target already contains its resolved transport path. Persisting it increases migration and stale-tab complexity.
- **Probe Gateway first, then local:** adds network calls and changes ambiguous-target precedence without solving provenance.

### 2. Use a platform-neutral lexical absolute-path classifier

Introduce one small pure classifier used by renderer fallback. It recognizes:

- POSIX absolute: leading `/`;
- Windows drive-absolute: `^[A-Za-z]:[\\/]`;
- UNC: leading `\\`;
- everything else as relative for the existing join policy.

`X:relative.txt` intentionally does not match drive-absolute syntax. `file:` URLs remain in the existing dedicated URL branch and are not fed through this classifier.

**Why:** The renderer may run on a different OS from the Gateway, so host-native `path.isAbsolute()` would apply the wrong platform's semantics.

**Alternatives rejected:**

- **Node `path.isAbsolute()`:** evaluates according to the Desktop host, not the Gateway path format.
- **Normalize all backslashes to slashes:** can alter valid POSIX filenames and UNC semantics.
- **Only add a drive-letter regex:** leaves UNC paths vulnerable to the same relative join.

### 3. Preserve listed path bytes through remote FS request construction

The normalized Gateway-owned `PreviewTarget.path` remains the exact path returned by `/api/fs/list`. Existing `encodeURIComponent` request construction remains unchanged. The implementation must not derive the filesystem path back from `file://` URL pathname when `target.path` is available.

**Why:** The Gateway is authoritative for its own path syntax; the Desktop transport only needs safe URL encoding.

### 4. Reject leading-slash Windows drive syntax at the Gateway

On Windows, `_fs_path()` rejects the exact lexical pattern `^/[A-Za-z]:[\\/]` with HTTP 400 before `Path.resolve()` or filesystem access. Canonical drive paths remain accepted.

**Why:** The primary Desktop fix eliminates malformed production requests. Rejection is safer than canonicalization because `/X:/...` is outside the listing/read round-trip contract and accepting it would expand API syntax. It also prevents silent retargeting under process CWD.

**Alternative rejected:** Narrowly stripping one slash would make the malformed request work, but could hide future producer bugs and creates a second accepted representation for the same filesystem path.

### 5. Test the authority boundary, not only helper output

Coverage is layered:

1. Pure classifier and `localPreviewTarget` matrix.
2. Normalization test proving Gateway authority bypasses Electron and local-first does not.
3. Remote request test proving exact encoded path, connection ID, and profile.
4. Gateway tests proving canonical paths are accepted and `/X:/...` fails with 400 on Windows semantics.
5. Integration harness from listed entry through read endpoint with a remote-only fixture.
6. Cross-host E2E with rendered-content checksum.

## Risks / Trade-offs

- **[Incorrect provenance at a caller forwards a local path remotely]** → Gateway authority is opt-in, restricted to paths sourced from remote Files/Review data, and covered by negative local-first tests.
- **[Lexical classifier misclassifies drive-relative or UNC syntax]** → Table-driven tests include `X:\`, `X:/`, `X:relative`, POSIX, UNC, `../`, Unicode, and spaces.
- **[Gateway rejection breaks a client currently sending `/X:/...`]** → No canonical listing path uses that form; add explicit tests and release-note the stricter invalid-input behavior.
- **[Same-host tests pass by local duplication and hide regression]** → Integration and E2E fixtures must be absent on the Desktop host.
- **[Review paths have different provenance than Files paths]** → Mark authority only where review data is confirmed to come from the active remote Gateway; retain default local-first otherwise.

## Migration Plan

1. Add failing tests for Windows drive paths, authority selection, and Gateway `/X:/...` rejection.
2. Add the lexical classifier and authority option without changing default behavior.
3. Opt remote Files and eligible Review tree callers into Gateway authority.
4. Run focused Desktop and Gateway suites, then full relevant test suites.
5. Run cross-host Windows Gateway E2E with a remote-only fixture and record endpoint, path category, connection/profile scope, status, and content checksum.
6. Roll back the caller opt-in and classifier together if remote preview regresses; Gateway rejection can be rolled back independently because it is defense in depth.
