# Tasks: Desktop Native Token 401 Force-Refresh and Single-Replay

## Milestones

- [x] 1. Core Forced Refresh & Rejected-Bearer Awareness <!-- id: 1 -->
  - [x] 1.1 Support `rejectedBearer` in `native-token-refresh.ts` and reuse current bearer when storage already differs <!-- id: 1.1 -->
  - [x] 1.2 Preserve one in-flight refresh per gateway with bearer tracking <!-- id: 1.2 -->
  - [x] 1.3 Prevent stale 401 from clearing or rotating newer session credentials <!-- id: 1.3 -->
  - [x] 1.4 Update `executeWithNativeBearerSingleReplay` to pass `initialBearer` as `rejectedBearer` and replay only when rotated <!-- id: 1.4 -->

- [x] 2. Production Caller Wiring in Electron Main <!-- id: 2 -->
  - [x] 2.1 Wire `fetchJsonForBackend` to pass `rejectedBearer` to refresher <!-- id: 2.1 -->
  - [x] 2.2 Wire `hermes:api` handler to pass `rejectedBearer` to refresher <!-- id: 2.2 -->

- [x] 3. Automated Concurrency & Unit Regression Suites <!-- id: 3 -->
  - [x] 3.1 Unit tests for rejected-bearer reuse and stale 401 protection in `native-token-refresh.test.ts` <!-- id: 3.1 -->
  - [x] 3.2 Automated staggered-concurrency regression test in `native-token-refresh.test.ts` <!-- id: 3.2 -->
  - [x] 3.3 End-to-end staggered-concurrency regression test in `native-auth-decisions.test.ts` <!-- id: 3.3 -->
  - [x] 3.4 Verify candidate against `review-native-401-staggered-failure.mts` and run complete Electron suites <!-- id: 3.4 -->
