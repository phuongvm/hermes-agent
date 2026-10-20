## ADDED Requirements

### Requirement: Slash command-name Space is a literal delimiter by default
While the slash-completion popover is open at the command-name stage (the slash query is non-empty and contains no space), the Desktop composer SHALL treat Space as a literal delimiter unless the user has explicitly moved the highlight with ArrowUp or ArrowDown since the query last changed. The composer SHALL NOT call `preventDefault()` on that Space keydown, and SHALL NOT replace the typed token with a chip or with a completion row. Moving the highlight with the mouse SHALL NOT count as an explicit selection.

#### Scenario: Space after a typed command name with the default highlight
- **WHEN** the user types `/zeta-alpha` in the composer, the popover shows matching rows with the default first row highlighted, and the user presses Space without pressing an arrow key
- **THEN** the Space keydown SHALL NOT be `defaultPrevented`
- **AND** the editor SHALL contain the plain text `/zeta-alpha` with no `[data-ref-text]` chip

#### Scenario: Space after a partial command name does not complete to an un-chosen row
- **WHEN** the user types `/zeta-a` and the first highlighted row is `/zeta-alpha`, and the user presses Space without pressing an arrow key
- **THEN** the Space keydown SHALL NOT be `defaultPrevented`
- **AND** the editor SHALL contain the plain text `/zeta-a` with no chip

#### Scenario: Space after an inline skill reference in prose
- **WHEN** the user types `look at /zeta-alpha` (an inline mid-message slash reference) and presses Space without pressing an arrow key
- **THEN** the Space keydown SHALL NOT be `defaultPrevented`
- **AND** no chip SHALL be inserted

#### Scenario: Hover does not make Space accept
- **WHEN** the user types `/zeta-` and hovers the second popover row with the mouse, then presses Space
- **THEN** the Space keydown SHALL NOT be `defaultPrevented`

### Requirement: Explicit arrow selection accepts on Space, Enter or Tab
When the user has explicitly moved the slash-popover highlight with ArrowUp or ArrowDown at the command-name stage, the composer SHALL accept exactly the highlighted row on Space, Enter, or Tab. It SHALL call `preventDefault()` on that keydown and commit that row, not the default first row.

#### Scenario: Arrow then Space commits the arrowed row
- **WHEN** the user types `/zeta-`, presses ArrowDown once so the second row `/zeta-beta` is highlighted, and presses Space
- **THEN** the Space keydown SHALL be `defaultPrevented`
- **AND** the editor SHALL contain a chip whose `data-ref-text` is `/zeta-beta`

#### Scenario: Arrow then Enter commits the arrowed row
- **WHEN** the user types `/zeta-`, presses ArrowDown once, and presses Enter
- **THEN** the Enter keydown SHALL be `defaultPrevented`
- **AND** the editor SHALL contain a chip whose `data-ref-text` is `/zeta-beta`

#### Scenario: Tab accepts the highlight without arrowing
- **WHEN** the user types `/zeta-` and presses Tab without pressing an arrow key
- **THEN** the Tab keydown SHALL be `defaultPrevented`
- **AND** the editor SHALL contain a chip whose `data-ref-text` is the highlighted first row `/zeta-alpha`

#### Scenario: Typing after an arrow drops the explicit selection
- **WHEN** the user types `/zeta-`, presses ArrowDown, then types another character so the query changes, then presses Space
- **THEN** the Space keydown SHALL NOT be `defaultPrevented`

### Requirement: Space and Enter boundaries outside the command-name stage are preserved
The composer SHALL keep the existing Space and Enter semantics outside the command-name stage:
- Space SHALL NOT accept a completion in a free-text argument stage (`freeTextArgStage === true`), even after an explicit arrow selection.
- Space SHALL accept the matching finite option in an `options` argument stage.
- Space SHALL NOT accept an `@` mention completion or a bare `/ ` query.
- Enter SHALL accept unless a free-text argument stage is active without an explicit selection.
- Tab SHALL always accept.

#### Scenario: Free-text slash argument is never converted by Space
- **WHEN** the slash query is in a free-text argument stage (for example `goal ship the`), with or without an explicit arrow selection, and the user presses Space
- **THEN** `acceptsTriggerCompletion` SHALL return `false` for Space

#### Scenario: Finite option argument still accepts on Space
- **WHEN** the slash query is in an `options` argument stage (for example `personality alic`) and no arrow key was pressed
- **THEN** `acceptsTriggerCompletion` SHALL return `true` for Space, Enter, and Tab

#### Scenario: Mentions and a bare slash take a literal space
- **WHEN** the trigger kind is `@` with query `src/comp`, or the slash query is empty
- **THEN** `acceptsTriggerCompletion` SHALL return `false` for Space

#### Scenario: Enter in free text sends prose unless a row was arrowed to
- **WHEN** the slash query is in a free-text argument stage
- **THEN** `acceptsTriggerCompletion` SHALL return `false` for Enter without an explicit selection and `true` for Enter with one

### Requirement: Focused controls keep keyboard ownership of Space
Space pressed while focus is on a focused activating control (a button, link, summary, or other `isActivateOnEnterTarget` element) SHALL be left to that control for native keyboard activation. Window-level type-to-focus SHALL NOT redirect that Space into the composer. Letter keys on the same control SHALL continue to route to the composer.

#### Scenario: Space on a focused button activates the button
- **WHEN** focus rests on a `<button>` outside the composer and the user presses Space
- **THEN** `composerFocusKeysAllowed` SHALL return `false` for that Space keydown

#### Scenario: A letter on a focused button still reaches the composer
- **WHEN** focus rests on a `<button>` outside the composer and the user presses `a`
- **THEN** `composerFocusKeysAllowed` SHALL return `true` for that keydown
