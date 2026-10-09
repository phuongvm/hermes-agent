## 1. Specification & Contract Setup
- [x] 1.1 Create OpenSpec change directory `integrate-crew-desktop-dashboard` with proposal, delta spec, design, and tasks
  - Proof (cwd: O:/workspaces): `node -e "assert=require('assert'); fs=require('fs'); assert(fs.existsSync('oss/hermes-agent/openspec/changes/integrate-crew-desktop-dashboard/proposal.md')); console.log('OK')"`

## 2. Crew Desktop Plugin Implementation
- [x] 2.1 Author `oss/crew/desktop/plugin.js` implementing Hermes plugin contract (`ROUTES_AREA` for `/crew` and `SIDEBAR_NAV_AREA` with `order: 55` below OpenSpec)
  - Proof (cwd: O:/workspaces): `node -e "const fs=require('fs'); const code=fs.readFileSync('oss/crew/desktop/plugin.js', 'utf8'); if (!code.includes('order: 55') || !code.includes('/crew')) process.exit(1); console.log('OK')"`
- [x] 2.2 Sync `oss/crew/desktop/plugin.js` to runtime directory `_config/agent4070/hermes/plugins/crew/desktop/plugin.js`
  - Proof (cwd: O:/workspaces): `node -e "const fs=require('fs'); if (!fs.existsSync('_config/agent4070/hermes/plugins/crew/desktop/plugin.js')) process.exit(1); console.log('OK')"`

## 3. Desktop Sidebar Navigation & Ordering Verification
- [x] 3.1 Verify navigation items ordering in `apps/desktop` ensuring contributed nav item with order 55 sorts below OpenSpec (order 50)
  - Proof (cwd: O:/workspaces): `npm --prefix oss/hermes-agent/apps/desktop test -- src/app/chat/sidebar/navigation.test.tsx`
- [x] 3.2 Verify Desktop typecheck passes cleanly across all tsconfigs
  - Proof (cwd: O:/workspaces): `npm --prefix oss/hermes-agent/apps/desktop run typecheck`

## 4. Web Dashboard Parity Verification
- [x] 4.1 Verify `oss/crew/dashboard/manifest.json` tab configuration matches `after:openspec`
  - Proof (cwd: O:/workspaces): `node -e "const m=JSON.parse(require('fs').readFileSync('oss/crew/dashboard/manifest.json')); if (m.tab.position !== 'after:openspec' || m.tab.path !== '/crew') process.exit(1); console.log('OK')"`

## 5. End-to-End Validation & OpenSpec Sign-Off
- [x] 5.1 Run OpenSpec strict validation on the change
  - Proof (cwd: oss/hermes-agent): `C:\nvm4w\nodejs\openspec.cmd validate integrate-crew-desktop-dashboard --strict`

### Execution Evidence Log (2026-10-08)
- Task 1.1: `proposal.md` existence asserted (Exit code 0).
- Task 2.1: `oss/crew/desktop/plugin.js` authored with ESM export, `order: 55`, and route `/crew` (Exit code 0).
- Task 2.2: Synced to `_config/agent4070/hermes/plugins/crew/desktop/plugin.js` and `desktop-plugins/crew/` (Exit code 0).
- Task 3.1: Vitest suite `src/app/chat/sidebar/navigation.test.tsx` passed 5/5 tests (including out-of-sequence order 55 vs 50 registry sort) in 227ms (Exit code 0).
- Task 3.2: `npm run typecheck` clean across all 4 tsconfigs (Exit code 0).
- Task 4.1: Dashboard manifest validated with `position: after:openspec` and `path: /crew` (Exit code 0).
- Task 5.1: `openspec validate integrate-crew-desktop-dashboard --strict` exited 0 (Change is valid).
