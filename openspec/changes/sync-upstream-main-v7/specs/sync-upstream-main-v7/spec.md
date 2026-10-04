# Sync Upstream Main v7 Specification

## ADDED Requirements

### Requirement: Hermetic Worktree Isolation
The upstream merge SHALL be performed only in an external worktree (`O:/workspaces/wt-hermes-sync-daily`) and SHALL NOT modify the live checkout (`O:/workspaces/oss/hermes-agent`), production ports (9119, 8642), `state.db`, `kanban.db`, or the production Honcho workspace before full verification.

#### Scenario: Merge performed in worktree
- **GIVEN** live checkout is clean at `origin/main`
- **WHEN** the merge is executed
- **THEN** all unmerged files exist only in `O:/workspaces/wt-hermes-sync-daily` and live production is untouched.

### Requirement: Preservation of Invariants INV-1 through INV-9
The reconciled tree SHALL preserve all 9 core invariants:
1. INV-1: Windows single-thread OpenBLAS prewarm and `HERMES_MAX_ITERATIONS` enforcement.
2. INV-2: `SUPPORTS_MESSAGE_EDITING = False`, 30s/60s WAN WebSocket ping keepalive, and Windows file URI path normalizer.
3. INV-3: 401 reauth modal latch (`reauthModalLatch`), single-replay bearer retry, and `resolveHermesVersionLadder` fallback.
4. INV-4: Pinned `@assistant-ui/tap: 0.9.8` inside `apps/desktop` with frozen `EMPTY_OBJECT`.
5. INV-5: `PYTHONUNBUFFERED=1` in service generation.
6. INV-6: Coalesced profile read 10s timeout with task eviction and fallback to `_fallback_profile_dicts`.
7. INV-7: `MAX_EXPONENT = 15` backoff limit and active self-repo mutation guard.
8. INV-8: Windows NSIS installer format admission across `electron-builder.config.cjs`, `run-electron-builder.mjs`, `prepare-packaging-tools.mjs`, and `stage-gateway-connection.mjs` in `build.mjs`.
9. INV-9: Electron pinned to `40.10.2` and clean `npm audit` on desktop workspace.

#### Scenario: Verification checks pass
- **GIVEN** the merged tree
- **WHEN** typecheck, invariant tests, NSIS installer build, and shadow E2E run
- **THEN** all exit codes are 0.

### Requirement: Reconciled File System Route Security and Interoperability
The web server files route SHALL protect configured hidden directories (returning 403 Forbidden) and normalize Windows root paths to the default working directory, while supporting MSYS path translations and percent-encoded non-ASCII path fallbacks.

#### Scenario: Files endpoint security and decoding
- **GIVEN** a request to list or read files under a configured hidden path
- **WHEN** `/api/fs/list` or `/api/fs/read-text` is called
- **THEN** 403 Forbidden is returned.
- **AND GIVEN** a double percent-encoded non-ASCII path
- **WHEN** `/api/fs/read-text` is called
- **THEN** 200 OK is returned with the decoded file content.
