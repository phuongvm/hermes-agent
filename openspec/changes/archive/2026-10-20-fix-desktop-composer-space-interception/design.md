## Context

The Hermes Desktop composer is a rich `contentEditable` editor in `apps/desktop/src/app/chat/composer/index.tsx`. While the trigger popover is open, `handleEditorKeyDown` (`index.tsx:L664-L1052`) decides whether a key belongs to the popover or the editor:

1. ArrowUp/ArrowDown (`index.tsx:L796-L811`) call `moveTriggerActive`, which sets `triggerActiveExplicit = true` (`hooks/use-composer-trigger.ts:L250-L253`).
2. `acceptsTriggerCompletion` (`composer-utils.ts:L187-L205`) classifies the key as an accept gesture, or not.
3. On accept, the row comes from `implicitSlashAcceptIndex` for Space/Enter, or from the highlight for Tab (`index.tsx:L831-L836`). When a row resolves, the handler calls `event.preventDefault()` and `replaceTriggerWithChip` (`index.tsx:L839-L845`).

`triggerActiveExplicit` resets to `false` whenever the trigger kind or query changes (`hooks/use-composer-trigger.ts:L142-L145`, `L192-L194`). Hovering a row with the mouse (`onHover={setTriggerActive}`, `index.tsx:L1408`) moves the highlight but does **not** set the explicit flag.

## 1. Causal boundary (resolves F-05)

Each layer named in the first revision was traced separately. Only Layer A reproduces the symptom. The others are recorded so the implementation does not widen.

| Layer | Deterministic sequence | Expected event ownership | Observed | Disposition |
|---|---|---|---|---|
| **A. Slash autocomplete** | Focus composer → type `/clean` (or `look at /skill`, or `/com` while `/compress` is the first row) → press Space with no arrow key | Editor: insert `' '` | `acceptsTriggerCompletion` → `true` (`composer-utils.ts:L204`); `implicitSlashAcceptIndex` resolves the exact or unique-prefix row; `preventDefault()` at `index.tsx:L840`; token replaced by a chip or by an un-chosen completion | **In scope.** The only proven defect. |
| **B. Type-to-focus on a focused control** | Focus a `<button>` (send, suggestion pill) → press Space | Focused button: native activation (`apps/desktop/AGENTS.md:L128-L129`, `DESIGN.md:L498-L500`) | `composerFocusKeysAllowed` returns `false` for Space on an `isActivateOnEnterTarget` (`lib/keybinds/composer-focus-keys.ts:L188-L189`), so the button activates. This is deliberate (commit `2a6debe5eb9` "leave Space activation to focused controls"), asserted by `composer-focus-keys.test.ts:L169-L179`. A letter key still routes to the composer. | **Out of scope. Not a defect.** No behavior change; the existing test stays as a regression guard (§3.3). |
| **C. Sidebar session-row sortable** | Focus the ⋯ button inside a session row → press Space | Focused ⋯ button | Contained since #83617: only the grabber carries dnd-kit's keyboard activator (`sidebar/reorderable-list.tsx:L64-L72`), proven by `sidebar/session-row.test.tsx:L337-L355` (Space on ⋯ not `defaultPrevented`; Space on the grabber still arms a drag). | **Out of scope.** Already fixed; guarded by the existing tests. |
| **D. Profile-rail sortable** | Focus composer with the profile rail mounted → press Space | Editor | Source analysis of `@dnd-kit/core` 6.3.1 (`node_modules/@dnd-kit/core/dist/core.esm.js`): the `KeyboardSensor` activator is a React `onKeyDown` on the profile square itself (`profile-switcher.tsx:L1499-L1500`), and it bails unless `event.target` is the activator node (`core.esm.js:L1357-L1362`). The document-level keydown listener exists only after activation (`attach`, `core.esm.js:L1154-L1158`), and Space/Enter/Tab end the drag (`defaultKeyboardCodes`, `core.esm.js:L1098-L1102`). A keydown targeting the composer never reaches the profile activator. No reproduction exists. | **Out of scope.** If a runtime reproduction is found later, it gets its own change with its own tests. |

Scope rule for implementation: change only the Layer A predicate. Run the Layer B/C guard tests unchanged.

## Goals / Non-Goals

**Goals:**
- At the slash **command-name stage**, Space inserts a literal delimiter unless the user explicitly picked a row with the arrow keys.
- Arrow-then-Space accepts exactly the arrowed row.
- Pin the existing Tab, Enter, free-text, options-argument, `@` and bare-`/` semantics with tests.
- Keep focused-control Space activation and dnd-kit drag-handle activation exactly as they are.

**Non-Goals:**
- Any change to `composer-focus-keys.ts`, the type-to-focus routing, or the sidebar/profile dnd-kit sensors (Layers B–D).
- Any change to `index.tsx`, `use-composer-trigger.ts`, chip rendering, or slash-command dispatch.
- Treating mouse hover as an explicit selection.

## 2. Decisions

### D1. Space at the command-name stage requires an explicit pick (resolves F-03)

The new `acceptsTriggerCompletion` Space rule:

```ts
// Space is slash-only (an `@` mention takes a literal space), gated to a
// non-empty query (a bare `/ ` types a space), and never takes over prose.
if (key !== ' ' || kind !== '/' || !query.trim() || freeTextArgStage) {
  return false
}

// At the command-name stage the highlight is only the list's default first
// row, so Space is the delimiter before arguments unless the user arrowed to
// a row deliberately. In a finite options stage Space still picks the match.
return slashArgStage(query) || activeExplicit
```

`slashArgStage` already exists in `composer-utils.ts` (`L112`). The Tab and Enter branches (`L194-L200`) are unchanged.

Full truth table for Space with `kind === '/'`:

| Stage | `query` example | `freeTextArgStage` | `activeExplicit` | Space accepts? | Change vs. today |
|---|---|---|---|---|---|
| bare | `''` | false | any | no | unchanged |
| command-name | `clean`, `com`, inline `skill` | false | **false** | **no** (literal space) | **changed** (was yes) |
| command-name | `com` | false | **true** | yes (arrowed row) | unchanged |
| options arg | `personality alic` | false | any | yes (matching option) | unchanged |
| free-text arg | `goal ship the` | true | any | no | unchanged |

For `kind === '@'`, Space never accepts (unchanged).

*Rationale:* in Slack, Discord and VS Code command palettes, typing a space after a command name moves into the argument; it does not swap the token. The current code already applies the "highlight is a suggestion until you arrow" principle to Enter in free text (`composer-utils.ts:L177-L186`). D1 applies it to Space at the command-name stage, where the default highlight is the least trustworthy.

*Why arrow-then-Space is kept (not removed):* `implicitSlashAcceptIndex` already honors an explicit highlight first (`composer-utils.ts:L138-L140`, #98535), so Space and Enter agree on the same arrowed row and the highlight never lies about what Space will do.

*Alternative rejected:* removing Space as an accept key entirely. That breaks the established `options`-stage pick (`/personality alic` + Space → `alice`) asserted by `composer-utils.test.ts:L78-L82`, which is outside this defect.

*Alternative rejected:* requiring Escape before Space. It breaks typing rhythm.

### D2. No change to the keydown handler

When `acceptsTriggerCompletion` returns `false` for Space, `index.tsx:L825` skips the accept block. Space then reaches the Backspace/Escape checks (no match). Next comes the empty-list typed-directive branch at `index.tsx:L875-L892`, which requires `slashArgStage(trigger.query)` and therefore does not fire at the command-name stage. The handler returns without `preventDefault()`, the browser inserts the space, and keyup's `refreshTrigger` (`hooks/use-composer-trigger.ts:L47-L56`) re-detects the trigger. A no-arg or text command's popover closes (`use-composer-trigger.ts:L176-L184`). An `options`/`mixed` command's popover moves to its argument list. No edit to `index.tsx` is needed, so none is made.

### D3. Focused controls keep owning Space (resolves F-01)

The first revision's "type-to-focus space delegation" requirement is withdrawn. A first Space on a focused native button cannot be told apart from keyboard activation, and repurposing it would break keyboard access and contradict the keyboard-ownership rule (`apps/desktop/AGENTS.md:L128-L129`, `DESIGN.md:L498-L500`). The delta spec instead carries a guard requirement: Space on a focused activating control is left to that control. It is proven by the existing, unmodified assertion at `composer-focus-keys.test.ts:L176`. No exception to the architecture is introduced, so no accessibility exception rationale is needed.

### D4. Sensor containment is not part of this change (resolves F-02)

The first revision's "drag sensor keyboard event containment" SHALL is withdrawn, not left unplanned. Per §1 rows C and D, the session-row path is already contained and tested, and the profile-rail path has no reproduction: its activator is target-bound by dnd-kit itself. No sensor source is touched. The existing `session-row.test.tsx` cases run as regression guards in task 3.3, so keyboard drag accessibility (Space on the real grabber still arms a drag) is re-proven on the candidate.

### D5. Proof through the real editor event path (resolves F-04)

Unit tests on `acceptsTriggerCompletion` alone cannot show that `preventDefault()` is absent or present. A new integration test, `apps/desktop/src/app/chat/composer/slash-space-accept.test.tsx`, renders the **real** `ChatBar`. It reuses the provider harness of `paste-url-is-text.test.tsx:L1-L78` (`AssistantRuntimeProvider` + `MemoryRouter` + `I18nProvider`) with a stub gateway:

- `request('commands.catalog')` resolves a fixed catalog of skill-style commands unknown to the desktop registry (for example `/zeta-alpha`, `/zeta-beta`, `/zeta-gamma`). These have `argumentMode === null`, so an accept commits a chip rather than expanding to options.
- `request('complete.slash', { text })` resolves the catalog rows whose command starts with `text`.
- Any other method resolves `{}`.

The test pins `isContentEditable` on the editor as `paste-url-is-text.test.tsx:L81-L84` does. It types by `renderComposerContents` plus an `input` event, so `handleEditorInput` → keyup → `refreshTrigger` run. It waits for the popover row to render, then dispatches `fireEvent.keyDown(editor, { key: ' ', code: 'Space' })`. It asserts the boolean return of `fireEvent` (`false` ⇔ `defaultPrevented`), `editor.querySelector('[data-ref-text]')`, and `composerPlainText(editor)`.

jsdom does not perform the browser's default text insertion. For rejected paths, "a literal space is inserted" is therefore proven as: the keydown is **not** `defaultPrevented`, **and** the typed token is still plain text with no chip. This is the same evidence standard used by `session-row.test.tsx:L344`.

## Risks / Trade-offs

- [Risk] Users who relied on Space completing a partial command name (`/com` → `/compress`) must use Tab, Enter, or Arrow-then-Space. → *Mitigation:* Tab and Enter are unchanged. Arrow-then-Space is kept, and the popover stays visible until the space is typed.
- [Risk] A future refactor of `implicitSlashAcceptIndex` could make arrow-then-Space pick a different row than Enter. → *Mitigation:* the integration test asserts the committed chip's `data-ref-text` equals the arrowed row, not the default first row.
- [Risk] Layer D is ruled out by source analysis, not by a runtime test. → *Mitigation:* it is recorded as a non-goal with its evidence. Any runtime reproduction opens a separate change rather than widening this one.
