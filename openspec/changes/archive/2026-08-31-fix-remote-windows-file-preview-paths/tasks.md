## 1. Lock the Regression Baseline

- [x] 1.1 Add table-driven `localPreviewTarget` tests for POSIX absolute, Windows backslash and forward-slash drive-absolute, UNC, drive-relative, ordinary relative, parent-relative, spaces, and Unicode paths; verify the new Windows drive + `cwd` case fails before implementation and all control cases state expected behavior.
- [x] 1.2 Add normalization tests proving an explicitly Gateway-owned target in remote mode does not call Electron `normalizePreviewTarget`, while the default local-first target still does; verify both authority branches with mock call assertions.
- [x] 1.3 Add remote filesystem request assertions for exact encoded path, active `connectionId`, and profile; verify the focused Desktop test reproduces the malformed pre-fix request or otherwise fails on the unchanged-path contract.
- [x] 1.4 Add Windows Gateway tests for canonical drive paths and exact `/X:/...` input; verify canonical paths reach the intended fixture and leading-slash drive syntax fails with HTTP 400 before filesystem access.

## 2. Implement Authority-Aware Desktop Normalization

- [x] 2.1 Add a small platform-neutral absolute-path classifier in `apps/desktop/src/lib/local-preview.ts` that preserves POSIX, drive-absolute, and UNC forms while leaving `X:relative` relative; verify the table-driven path matrix passes.
- [x] 2.2 Add a default-local-first authority option to `normalizeOrLocalPreviewTarget` and bypass Electron-local existence probing only for Gateway-owned targets in active remote mode; verify authority branch tests pass without changing existing callers.
- [x] 2.3 Mark remote Files pane activation as Gateway-owned while retaining local behavior when the filesystem mode is local; verify Files pane tests cover both connection modes and the remote listed path remains unchanged.
- [x] 2.4 Mark eligible remote Review tree preview activation as Gateway-owned without changing local or ambiguous review paths; verify Review tree tests assert the correct authority for remote and local contexts.
- [x] 2.5 Verify transcript, tool, manual, picker, and dropped-file preview paths retain local-first behavior by running their existing tests plus explicit negative assertions that remote mode alone does not forward local-only targets.

## 3. Harden Windows Gateway Path Admission

- [x] 3.1 Reject exact leading-slash Windows drive syntax in `_fs_path()` on Windows before `Path.resolve()` while preserving canonical drive, POSIX, UNC, null-byte, and `file:` handling; verify focused Gateway path tests pass.
- [x] 3.2 Verify malformed `/X:/...` input returns HTTP 400 rather than 404 or a silently retargeted path across `/api/fs/read-text`, `/api/fs/read-data-url`, and download/read entry points that share `_fs_path()`.

## 4. Integration and Regression Verification

- [x] 4.1 Add an integration harness covering Windows `/api/fs/list` entry → Files pane activation → authority-aware normalization → `/api/fs/read-text`, with the fixture absent from the Desktop filesystem; verify the read path equals the listed path and content checksum matches.
- [x] 4.2 Run focused Desktop suites for local preview, remote filesystem routing, Files pane, Review tree, and preview rendering; record exact command, test count, and zero exit status.
- [x] 4.3 Run focused Gateway filesystem suites including canonical and malformed Windows path cases; record exact command, test count, and zero exit status.
- [ ] 4.4 Run Desktop typecheck/lint and the full relevant Desktop and Python test suites; record exact commands, pass counts, and any unrelated pre-existing failures without marking this task complete until change-related failures are zero.

## 5. Cross-Host E2E and Completion Evidence

- [ ] 5.1 On a Windows Gateway, create a text fixture that does not exist on the Desktop host and verify a different-host Windows Desktop opens it from the Files pane; capture endpoint, decoded path category, connection ID, profile, HTTP status, and rendered-content checksum without logging file content or secrets.
- [ ] 5.2 Verify the same remote-only Windows fixture from a Linux or macOS Desktop when that test host is available, or document the unavailable platform as an explicit release blocker rather than substituting unit evidence.
- [ ] 5.3 Run same-host Windows and remote POSIX regression controls, plus UNC when the environment supports it; verify existing preview behavior remains intact and document any unsupported UNC environment explicitly.
- [ ] 5.4 Confirm the remote fixture and temporary diagnostics are removed, `git diff --check` passes, only scoped files changed, and the OpenSpec tasks/spec requirements map to recorded test and E2E evidence before requesting review.
