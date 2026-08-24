## Context

Hermes Desktop operates as an Electron desktop application that communicates with Hermes backends. A backend can run locally (spawned by Desktop as `hermes serve --host 127.0.0.1 --port 0`) or remotely (connecting to a remote URL, such as `http://127.0.0.1:9119` for Dashboard or remote host instances).

To prevent process leaks on Windows and Unix systems when Electron quits abruptly, Desktop implements an ownership ledger in `apps/desktop/electron/backend-ownership.ts`. This ledger tracks spawned children using PID and start markers. On launch and connection reset, `reapOrphans()` inspects processes matching `backendCommandMatches()`:
```typescript
export function backendCommandMatches(command: unknown): boolean {
  return /(?:^|[\s/\\"])(?:hermes(?:\.exe)?|hermes_cli\.main|hermes_cli[/\\]main\.py)"?(?:\s+(?:--profile|-p)\s+\S+)?\s+(?:serve|dashboard)(?:\s|$)/i.test(
    String(command ?? '')
  )
}
```
If a matched process's parent PID is not the current Electron instance, Desktop concludes that the process was orphaned from a previous crashed Electron session and invokes `forceKillProcessTree(pid)` (`taskkill /T /F /PID <pid>` on Windows).

**The Fault Pattern**:
1. When Gateway is launched via `python -m hermes_cli.main --profile default gateway run --replace` (or via NSSM service `HermesGateway`), it matches parts of the CLI binary regex and can be misclassified if broad patterns match, or when stale backend entries carry the gateway's reused PID.
2. Furthermore, in `apps/desktop/electron/main.ts:13992`, `handleHermesApiRequest` delegates via `dispatchApiRequestRoute(request, ...)` where `req` is passed to the lambda arguments. In some internal error recovery and logging branches, free variable references to `req` instead of `request` or the lambda's formal parameter trigger `ReferenceError: req is not defined`.
3. This IPC crash causes the Desktop renderer to fail every settings or session query, entering a rapid restart loop where `reapOrphans()` is triggered repeatedly.

---

## Goals / Non-Goals

**Goals**:
1. Strictly restrict `backendCommandMatches` in `backend-ownership.ts` to only match `serve` and `dashboard --no-open` spawned specifically with Desktop markers, and explicitly exclude `gateway`, `cron`, `daemon`, and related background service commands.
2. Fix all scope variable references in `apps/desktop/electron/main.ts` so `handleHermesApiRequest` never raises `ReferenceError`.
3. Add exhaustive unit test assertions in `backend-ownership.test.ts` to permanently prevent regressions where Gateway or other commands match orphan filters.
4. Ensure `npm run typecheck` passes cleanly across the Desktop codebase.

**Non-Goals**:
- Redesigning the entire Electron backend pooling system.
- Merging Dashboard and Gateway into a single process.
- Modifying how Gateway itself handles messaging platform polling.

---

## Decisions

### D1: Explicit Negative Guard in `backendCommandMatches`

**Decision**: In `apps/desktop/electron/backend-ownership.ts`, add explicit checks to reject any command containing `gateway`, `cron`, `tools`, `doctor`, or other non-backend CLI subcommands before checking for `serve`/`dashboard`.

**Rationale**: Regex matching for CLI commands on Windows can be tricky due to complex quoting, venv shims, and path variations. A defense-in-depth approach (reject non-backend subcommands first, then match strict backend syntax) ensures background services are never accidentally matched.

### D2: Consistent Formal Parameter Naming in `main.ts` IPC Handlers

**Decision**: Standardize on `request` as the single payload variable name in `handleHermesApiRequest` and its delegate closures (`resolveRegistry`, `resolveLegacy`), removing any shadowed or undeclared `req` identifiers.

**Rationale**: TypeScript typechecking and clean parameter scoping prevent runtime identifier errors from slipping past compilation.

---

## Risks / Trade-offs

| Risk | Mitigation |
| :--- | :--- |
| Desktop fails to reap actual orphaned `serve` processes | Matcher continues to match valid `serve` and `dashboard --no-open` patterns exactly as intended. Unit tests cover all positive and negative permutations. |
| Rebuilding Electron bundle is needed | All changes reside in TypeScript sources under `apps/desktop/electron/` and are verified with `npm run typecheck` and test suites. |
