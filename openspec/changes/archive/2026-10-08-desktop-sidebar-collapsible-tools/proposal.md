## Why

The top of the Hermes Desktop left sidebar currently has multiple secondary/utility links (Capabilities, Messaging, Cron, Settings, Webhooks, etc.) that take up valuable vertical space and push daily primary navigation (Sessions, Projects, Pinned, Search) to the bottom of the sidebar. Reorganizing the sidebar to promote primary navigation to the top and grouping utility links into a collapsible "TOOLS" section (collapsed by default) provides immediate access to daily workflows while keeping utilities readily accessible on demand.

## What Changes

1. **Reorder Navigation Hierarchy**:
   - Promote primary navigation (Sessions, Projects, Pinned, Search) to the top of the left sidebar.
   - Keep New Session pinned at the top as the primary action entry point.
2. **Collapsible TOOLS Section**:
   - Group secondary utility navigation links (Capabilities, Messaging, Cron, Settings, etc.) inside a collapsible group labeled "TOOLS".
   - Default state is collapsed (`toggled / not expanded`) so the sidebar remains compact.
   - User can expand/collapse the section with clean local state.
3. **Preserve Navigation & Profile State**:
   - Ensure profile switching, session activation, and router links remain fully functional without regression.
   - Pass existing desktop test suite.

## Capabilities

### Modified Capabilities
- `desktop-sidebar-navigation`: Restructure left sidebar navigation hierarchy with collapsible TOOLS section.
