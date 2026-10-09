# Tasks: Crew Desktop Dedicated Tab & Tile Routing

## 1. Implementation Checklist

- [x] Task 1: Extend Desktop Types & Declarative Route Tile Support
  - Edit `oss/hermes-agent/apps/desktop/src/app/routes.ts`:
    - Add `asTile?: boolean` to `SidebarNavContribution`.
  - Edit `oss/hermes-agent/apps/desktop/src/app/types.ts`:
    - Add `asTile?: boolean` to `SidebarNavItem`.
  - Edit `oss/hermes-agent/apps/desktop/src/store/route-tiles.ts`:
    - Support `dir: TileDock = 'center'`.
    - Call `revealTreePane('route-tile:' + path)` to focus/front the tab.
  - Edit `oss/hermes-agent/apps/desktop/src/app/chat/sidebar/index.tsx`:
    - Pass `asTile: data.asTile` into `contributedNav`.
  - Edit `oss/hermes-agent/apps/desktop/src/app/session/hooks/use-session-actions/index.ts`:
    - In `selectSidebarItem`, check `if (item.asTile && item.route)` and call `openRouteTile(item.route, 'center')`.
  - Proof: `npm --prefix oss/hermes-agent/apps/desktop run typecheck`

- [x] Task 2: Configure Crew Plugin Navigation to Open as Dedicated Tab
  - Edit `oss/crew/desktop/plugin.js`:
    - Add `asTile: true` to the `SIDEBAR_NAV_AREA` contribution data.
    - Synchronize updated plugin to `_config/agent4070/hermes/plugins/crew/desktop/plugin.js` and `_config/agent4070/hermes/desktop-plugins/crew/plugin.js`.
  - Proof: `node --check oss/crew/desktop/plugin.js && node -e "const fs=require('fs'); const c=fs.readFileSync('oss/crew/desktop/plugin.js','utf8'); if (!c.includes('asTile: true')) process.exit(1); console.log('PASS')"`

- [x] Task 3: Add Navigation & Route Tile Unit Tests
  - Edit `oss/hermes-agent/apps/desktop/src/app/chat/sidebar/navigation.test.tsx`:
    - Add test verifying that clicking a sidebar item with `asTile: true` triggers route tile opening.
  - Proof: `npm --prefix oss/hermes-agent/apps/desktop test -- src/app/chat/sidebar/navigation.test.tsx`

- [x] Task 4: Complete OpenSpec Validation & Independent Verification
  - Execute strict OpenSpec schema validation.
  - Proof: `C:\nvm4w\nodejs\openspec.cmd validate crew-desktop-tab-tile --strict`
