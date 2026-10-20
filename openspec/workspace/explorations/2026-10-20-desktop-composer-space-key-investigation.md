# Desktop Composer Space Key Investigation & Root Cause Reproduction

- **Date**: 2026-10-20
- **Project**: `hermes-agent` (`O:\workspaces\oss\hermes-agent`)
- **Component**: `apps/desktop/src/app/chat/composer/` & `apps/desktop/src/lib/keybinds/`
- **Issue**: Intermittent failure when typing the spacebar in the message input (composer) on Hermes Desktop.

---

## 1. Executive Summary

Empirical investigation and test execution in the `apps/desktop` test suite revealed three distinct, reproducible technical root causes that explain why the spacebar intermittently fails or gets swallowed when typing messages in Hermes Desktop:

1. **Root Cause 1 (Primary - Slash/Autocomplete Trigger Interception)**:
   `acceptsTriggerCompletion` in `composer-utils.ts` treats the spacebar (`key === ' '`) as a completion-acceptance trigger for slash commands (`kind === '/'`). When a message begins with `/` (e.g. `/clean`, `/openspec-explore`, `/model`, `/crew`), or contains an inline slash reference (`"look at /skill"`), typing a space after the command token calls `event.preventDefault()` in `index.tsx`, preventing the space character from being inserted into the DOM and replacing the token with a `<span class="ref">` chip.

2. **Root Cause 2 (Secondary - Type-to-Focus Asymmetry on Controls)**:
   In commit `2a6debe5eb9` (`apps/desktop/src/lib/keybinds/composer-focus-keys.ts:189`), Space was deliberately excluded from type-to-focus when focus sits on an `isActivateOnEnterTarget` (buttons, suggestion pills, model selector, sidebar controls). If focus is lost to a button, typing letters (`a-z`) automatically focuses the composer and inserts the letter, but pressing `Space` is refused and absorbed by the focused button.

3. **Root Cause 3 (Tertiary - dnd-kit KeyboardSensor Global Space Leak)**:
   In `apps/desktop/src/app/chat/sidebar/profile-switcher.tsx:1500`, `{...listeners}` from `useSortable` is spread onto profile buttons. In `@dnd-kit/core`, pressing Space on a focused sortable control arms `KeyboardSensor`, which mounts a window-level capture listener that swallows the next `Space` press window-wide via `event.preventDefault()` (the exact bug pattern documented in `#83617`).

---

## 2. Empirical Reproduction & Proofs

### Root Cause 1: Slash Autocomplete Space Eater
- **Source Code**:
  - `apps/desktop/src/app/chat/composer/composer-utils.ts:202-205`:
    ```typescript
    // Space is slash-only (an `@` mention takes a literal space) and gated to a
    // non-empty query so a bare `/ ` still types a space.
    return key === ' ' && kind === '/' && Boolean(query.trim()) && !freeTextArgStage
    ```
  - `apps/desktop/src/app/chat/composer/index.tsx:839-846`:
    ```typescript
    if (accept) {
      ...
      if (item) {
        event.preventDefault()
        triggerKeyConsumedRef.current = true
        replaceTriggerWithChip(item, { descend: event.key === 'Tab' })
        return
      }
    }
    ```
- **Reproduction**:
  - User types `/openspec-explore` in the composer and presses Space to type prompt arguments.
  - `slashArgStage('/openspec-explore')` is `false` (no space in token yet).
  - `slashFreeTextArgStage` is `false`.
  - `acceptsTriggerCompletion` returns `true`.
  - `event.preventDefault()` is executed immediately.
  - **Outcome**: The literal space character is never entered into the `contentEditable` DOM. The command is converted into a chip element. If the user expects a space to separate the command from arguments, the space key appears broken/unresponsive.

### Root Cause 2: Focus on Buttons Absorbing Space
- **Source Code**:
  - `apps/desktop/src/lib/keybinds/composer-focus-keys.ts:188-190`:
    ```typescript
    // Space activates focused buttons too; it must not become a composer draft.
    return !((combo === 'enter' || event.key === ' ') && isActivateOnEnterTarget(event.target))
    ```
- **Reproduction**:
  - User submits a prompt or clicks a button (Send button, model selection pill, suggestion pill). Focus rests on the `<button>`.
  - User presses letter `a`: `composerFocusKeysAllowed` returns `true`, type-to-focus moves focus to the composer and enters `a`.
  - User presses `Space`: `composerFocusKeysAllowed` returns `false` because `event.key === ' '`.
  - **Outcome**: Focus remains on the button; space is never forwarded to the composer.

---

## 3. Comparison Matrix

| Scenario | Trigger / Cause | What Happens to Spacebar | User Experience |
| :--- | :--- | :--- | :--- |
| **Typing command / skill** (`/model`, `/clean`, `/openspec-explore`) | `acceptsTriggerCompletion` | `event.preventDefault()` called; chip committed | Space key does not insert `' '`, text replaced with chip |
| **Typing after focus on button** (suggestion pills, send button) | `composerFocusKeysAllowed` | Refused by `isActivateOnEnterTarget` check | Letters work via type-to-focus, Space does nothing |
| **Sidebar profile / session focus** | `@dnd-kit` `KeyboardSensor` | Global window capture swallows next Space | Space swallowed entirely across the application |

---

## 4. Next Steps & Recommendations

1. **Verify user context**: Confirm whether the user observed the spacebar failure when typing a slash command / skill (`/`), after clicking buttons/pills, or during pure free-text prose.
2. **If Slash Command Completion**:
   Adjust `acceptsTriggerCompletion` or `replaceTriggerWithChip` to ensure that pressing Space for commands that take arbitrary text or commands expecting trailing arguments appends a trailing space naturally or allows space to pass through rather than abruptly aborting the keystroke.
3. **If Button Focus**:
   Ensure that clicking in the composer dock or finishing message submission immediately reclaims true DOM focus on the `contentEditable` element via `focusInput()`.
