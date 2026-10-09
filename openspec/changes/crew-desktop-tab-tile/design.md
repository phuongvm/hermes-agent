# Design: Crew Desktop Dedicated Tab & Tile Routing

## 1. Context & Architecture Seams
In Hermes Desktop, there are two distinct ways to display a view:
1. **Workspace Page Navigation (`navigateToWorkspacePage`)**:
   - Replaces the `workspace` pane with the page route.
   - Sets `$workspaceIsPage = true`, causing `headerVeto` to hide the tabstrip.
   - Ideal for modal/settings or infrequent full-page admin views.
2. **Route Tile Pane (`openRouteTile`)**:
   - Registers a layout pane with prefix `route-tile:${path}` via `watchRouteTiles` (`pane-mirror.ts`).
   - Stacks as a native tab inside the active layout group when `dir === 'center'`.
   - Leaves `$workspaceIsPage = false`, so the center area tabstrip remains visible with both Chat and Route tabs.

## 2. Declarative Schema Design
To avoid anti-pattern hardcoded route checks in core hooks, we introduce an opt-in property `asTile?: boolean`:
```typescript
// apps/desktop/src/app/routes.ts
export interface SidebarNavContribution {
  codicon: string
  label: string
  path: string
  tier?: InterfaceTier
  asTile?: boolean
}

// apps/desktop/src/app/types.ts
export interface SidebarNavItem extends Tiered {
  id: SidebarNavId | (string & {})
  label: string
  icon: React.ComponentType<{ className?: string }>
  route?: string
  action?: 'new-session'
  keybindActionId?: string
  asTile?: boolean
}
```

## 3. Interaction & Routing Sequence
```
User clicks "Crew" (with asTile: true)
         │
         ▼
selectSidebarItem(item)
         │
         ├── Is item.asTile true?
         │         │
         │         YES ──> openRouteTile(item.route, 'center')
         │                 (Centers as tab, focuses active tab via revealTreePane)
         │
         └── NO  ──> navigateToWorkspacePage(navigate, item.route)
```
- If `'route-tile:/crew'` is not open, it is inserted into `$routeTiles` and docked into the center tab group.
- If `'route-tile:/crew'` is already open, `openRouteTile` is a no-op and `revealTreePane` fronts and focuses the existing tab.
- Note: Because `openRouteTile` internally invokes `revealTreePane('route-tile:' + path)`, the "Open in split" context-menu path (`chat/sidebar/navigation.tsx:166`) now reveals too.
- Closing the tab removes it from `$routeTiles` via standard pane closer.
