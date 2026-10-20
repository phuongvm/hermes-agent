## Why

Users of Hermes Desktop report that pressing Space in the chat composer sometimes does not behave as a space: the typed command token is replaced instead of being followed by a literal delimiter.

The proven, reproducible cause is the slash-completion accept rule. `acceptsTriggerCompletion` (`apps/desktop/src/app/chat/composer/composer-utils.ts:L202-L205`) treats Space as an accept key for **every** non-empty slash query that is not in a free-text argument stage, including the command-name stage, where the popover's highlight is only the list's default first row. The editor keydown path (`apps/desktop/src/app/chat/composer/index.tsx:L817-L845`) then calls `event.preventDefault()`, so the native keystroke never reaches the editor, and `replaceTriggerWithChip` swaps the typed token (`/clean`, an inline `/skill`, a partial `/com`) for a completion row the user never chose.

The first revision of this proposal also attributed the symptom to type-to-focus on buttons and to dnd-kit keyboard sensors. Reviewer audit `reviews/findings-fix-desktop-composer-space-interception-proposal.md` (F-01, F-02, F-05) showed that those layers either implement intended keyboard ownership or are already contained, and that no reproduction exists for them. They are **out of scope** for this change. The causal boundary is recorded in `design.md` §1.

## What Changes

- **Space at the command-name stage no longer accepts the default highlight.** `acceptsTriggerCompletion` returns `true` for Space in the command-name stage (the slash query has no space yet) only when the user has explicitly moved the highlight with ArrowUp/ArrowDown (`activeExplicit === true`). Otherwise Space is not consumed: `preventDefault()` is not called, and the editor inserts a literal space.
- **Arrow-then-Space accepts exactly the chosen row.** After ArrowUp/ArrowDown, Space commits the highlighted row through the existing explicit branch of `implicitSlashAcceptIndex` (`composer-utils.ts:L138-L140`), the same pick that Enter makes.
- **These boundaries are pinned unchanged and tested:**
  - Tab always accepts the highlight.
  - Enter accepts unless a free-text argument is in progress without an explicit pick.
  - Space never accepts in a free-text argument stage, even after arrowing.
  - Space keeps accepting a matching finite option in an `options` argument stage (`/personality alic` → `alice`).
  - `@` mentions and a bare `/ ` take a literal space.
  - Mouse hover over a row does not count as an explicit pick.
  - The empty-list "fully typed finite option" commit (`index.tsx:L875-L892`) is unchanged.
- **Focused-control keyboard activation is preserved.** `apps/desktop/src/lib/keybinds/composer-focus-keys.ts` is not modified. Space on a focused button keeps activating that button (commit `2a6debe5eb9`; `apps/desktop/AGENTS.md:L128-L129`; `apps/desktop/DESIGN.md:L498-L500`). Its existing assertion stays as a regression guard.
- **The tests are authored, not just re-run.** The `acceptsTriggerCompletion` unit expectations are extended to the new contract. A new integration test drives real Space `keydown` events through the real `ChatBar` editor and asserts `defaultPrevented` and the resulting editor text for every accepted and rejected Space path.

## Capabilities

### New Capabilities
- `desktop-composer-space-handling`: what Space does in the Desktop composer while the slash-completion popover is open (literal delimiter vs. explicit acceptance), and the guarantee that focused controls keep owning Space.

### Modified Capabilities
None.

## Impact

Source modified (implementation phase, inside a dedicated worktree only):
- `apps/desktop/src/app/chat/composer/composer-utils.ts`: the Space branch and doc comment of `acceptsTriggerCompletion` only.

Tests modified or added:
- `apps/desktop/src/app/chat/composer/composer-utils.test.ts`: add the command-name-stage Space cases.
- `apps/desktop/src/app/chat/composer/slash-space-accept.test.tsx` (new): real `ChatBar` keydown integration test.

Not modified (run as regression guards only):
- `apps/desktop/src/app/chat/composer/index.tsx`
- `apps/desktop/src/lib/keybinds/composer-focus-keys.ts` and `composer-focus-keys.test.ts`
- `apps/desktop/src/app/chat/sidebar/{index.tsx,profile-switcher.tsx,reorderable-list.tsx}` and `session-row.test.tsx`

User-visible trade-off: Space no longer turns a typed command name into the first suggestion. Tab, Enter, or Arrow-then-Space do that.
