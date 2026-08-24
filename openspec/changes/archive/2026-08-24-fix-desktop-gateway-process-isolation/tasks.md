## 1. Desktop Backend Ownership Isolation

- [x] 1.1 Update `backendCommandMatches` in `apps/desktop/electron/backend-ownership.ts` to explicitly reject commands containing `gateway`, `cron`, `tools`, and other non-backend CLI subcommands
- [x] 1.2 Ensure `backendCommandMatches` only accepts commands explicitly formatted as `serve` or `dashboard --no-open`
- [x] 1.3 Add unit test coverage in `apps/desktop/electron/backend-ownership.test.ts` asserting that `gateway run`, `gateway start`, `cron run`, and related commands evaluate to `false`

## 2. Desktop IPC Scope Resolution & Typecheck

- [x] 2.1 Audit `apps/desktop/electron/main.ts` in and around `handleHermesApiRequest` to standardize parameter naming on `request` and eliminate any `req` references
- [x] 2.2 Verify `dispatchApiRequestRoute` and its closures in `apps/desktop/electron/connection-config.ts` and `apps/desktop/electron/main.ts`
- [x] 2.3 Run `npm run typecheck` in `apps/desktop` to ensure clean TypeScript compilation across electron, src, and e2e packages

## 3. Verification & Gateway Stability

- [x] 3.1 Run `vitest run --project electron` in `apps/desktop` and ensure all backend ownership and connection tests pass
- [x] 3.2 Verify Gateway status and ensure Desktop connection checks do not trigger any `unclean exit` or `taskkill` events in `gateway-exit-diag.log`
