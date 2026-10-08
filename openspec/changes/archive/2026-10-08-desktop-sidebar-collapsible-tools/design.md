# Design: Hermes Desktop Left Sidebar Navigation Hierarchy & Collapsible Tools

## 1. Architectural Authority
- Primary architectural authorities for Hermes Desktop: `apps/desktop/AGENTS.md`, `apps/desktop/DESIGN.md`, and `apps/desktop/ENGINEERING.md`.
- Component hierarchy resides in `apps/desktop/src/app/`.

## 2. Navigation Layout Structure
- **Sidebar Top**: Pinned New Session button / header.
- **Primary Navigation Group**: Sessions, Projects, Pinned, Search.
- **Collapsible TOOLS Group** (default: collapsed):
  - Header with toggle control ("TOOLS").
  - Content: Capabilities, Messaging, Cron, Settings, Webhooks.

## 3. Implementation Details
- Component: Update sidebar navigation list in `apps/desktop/src/app/` to reflect new ordering.
- State: Local component toggle state or nanostore atom with default collapsed state.
- Testing: Vitest coverage for sidebar navigation structure and collapsible state.
