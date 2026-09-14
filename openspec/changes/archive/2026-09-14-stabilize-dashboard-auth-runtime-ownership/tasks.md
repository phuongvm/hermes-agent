# Tasks: Stabilize Dashboard Auth Runtime Ownership and Terminal Signed-Out Behavior

## 1. Backend Host-Auth Provider Ownership (Lane 1)

- [x] 1.1 Bind `SelfHostedOIDCProvider` session store path to host process home resolved at server initialization in `plugins/dashboard_auth/self_hosted/__init__.py`
- [x] 1.2 Guard `register_global_provider` in `hermes_cli/dashboard_auth/registry.py` and `PluginContext.register_dashboard_auth_provider` in `hermes_cli/plugins.py` against request-scoped or routed profile replacement
- [x] 1.3 Preserve process-level profile home binding when dashboard is launched explicitly with named profile (`-p <name>`)
- [x] 1.4 Maintain database compatibility with existing `dashboard-auth-sessions.db` files without schema changes or profile DB creation during routed rediscovery
- [x] 1.5 Add focused backend regression tests verifying root process remains bound to root DB after routed profile override, named profile process remains bound to profile DB, and concurrent rediscovery cannot replace active provider

## 2. Desktop Terminal-Auth Suspension and Recovery (Lane 2)

- [x] 2.1 Atomically transition normalized base URL into `reauth-required` / `signed-out` state and clear matching obsolete credentials on authoritative terminal rejection in `apps/desktop/electron/native-token-refresh.ts`
- [x] 2.2 Suppress background profile refreshes and error toasts in `apps/desktop/src/store/profile.ts` while base URL is in terminal signed-out state
- [x] 2.3 Defer and suppress active profile rail refresh listeners in `apps/desktop/src/app/chat/sidebar/use-profile-rail-refresh-on-active.ts` while signed out
- [x] 2.4 Pause project tree self-healing retry timers in `apps/desktop/src/app/right-sidebar/files/use-project-tree.ts` while signed out
- [x] 2.5 Enforce compare-and-set generation awareness ensuring stale in-flight 401 rejections do not clear or mutate newer login credentials
- [x] 2.6 Clear terminal signed-out state and resume/coalesce deferred background synchronization once upon confirmed sign-in
- [x] 2.7 Preserve explicit user logout (`oauthLogoutConnectionConfig`) semantics: server-side session revocation with clean local credential clearance
- [x] 2.8 Add focused Desktop regression tests for terminal transition, network suppression, stale rejection vs newer login, base URL isolation, and one-time resume

## 3. Verification and Integration

- [x] 3.1 Verify backend regression suite passes with zero failures and no secret leaks in logs
- [x] 3.2 Verify Desktop Electron vitest suites and TypeScript typecheck pass clean
- [x] 3.3 Validate OpenSpec change strictly with `openspec validate stabilize-dashboard-auth-runtime-ownership --strict`
