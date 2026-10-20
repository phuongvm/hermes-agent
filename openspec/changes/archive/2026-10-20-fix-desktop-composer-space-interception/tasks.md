## 0. Isolation

- [x] 0.1 Create a dedicated worktree. The production checkout stays on `main` (Zero-Checkout): `git -C O:/workspaces/oss/hermes-agent worktree add O:/workspaces/.worktrees/wt-fix-composer-space -b fix/desktop-composer-space-delimiter main`. Link `node_modules` and `apps/desktop/node_modules` into it as NTFS junctions, as the other hermes-agent worktrees do. All later tasks run inside this worktree. Proof: `git -C O:/workspaces/oss/hermes-agent rev-parse --abbrev-ref HEAD` prints `main` and `git -C O:/workspaces/.worktrees/wt-fix-composer-space rev-parse --abbrev-ref HEAD` prints `fix/desktop-composer-space-delimiter`.

## 1. Tests first (RED)

- [x] 1.1 Add unit cases to the `acceptsTriggerCompletion` block of `apps/desktop/src/app/chat/composer/composer-utils.test.ts` for every row of the `design.md` D1 truth table:
  - command-name Space with `activeExplicit: false` (`query: 'clean'`, `query: 'com'`) → `false`
  - command-name Space with `activeExplicit: true` → `true`
  - options-stage Space (`personality alic`) with `activeExplicit` false and true → `true`
  - free-text Space with `activeExplicit` false and true → `false`
  - `@` Space and bare `/ ` Space → `false`

  Keep the existing Enter/Tab/free-text/`@` cases unchanged. The existing `accepts on Enter / Tab / Space for a finite option list` case (options stage) must stay green. Proof: `cd O:/workspaces/.worktrees/wt-fix-composer-space/apps/desktop && npx vitest run src/app/chat/composer/composer-utils.test.ts` exits non-zero. Only the new command-name/`activeExplicit: false` Space cases fail.
- [x] 1.2 Create `apps/desktop/src/app/chat/composer/slash-space-accept.test.tsx`. It renders the real `ChatBar` with the provider harness of `paste-url-is-text.test.tsx` and a stub gateway: `commands.catalog` and `complete.slash` return Skills-group rows `/zeta-alpha`, `/zeta-beta`, `/zeta-gamma`; other methods return `{}`. Per `design.md` D5, drive real `input`/`keyDown` events on the editor and assert the `fireEvent.keyDown` return value (`defaultPrevented`), `[data-ref-text]`, and `composerPlainText`. Write one test per spec scenario of "Slash command-name Space is a literal delimiter by default" and "Explicit arrow selection accepts on Space, Enter or Tab":
  - default-highlight Space on `/zeta-alpha` → not prevented, no chip
  - partial `/zeta-a` Space → not prevented, no chip
  - inline `look at /zeta-alpha` Space → not prevented, no chip
  - hover second row then Space → not prevented
  - ArrowDown then Space → prevented, chip `/zeta-beta`
  - ArrowDown then Enter → prevented, chip `/zeta-beta`
  - Tab without arrow → prevented, chip `/zeta-alpha`
  - ArrowDown, type a character, then Space → not prevented

  Proof: `cd O:/workspaces/.worktrees/wt-fix-composer-space/apps/desktop && npx vitest run src/app/chat/composer/slash-space-accept.test.tsx` exits non-zero. Only the default-highlight, partial, inline, hover and type-after-arrow Space cases fail; the arrow/Enter/Tab cases pass.

## 2. Implementation (GREEN)

- [x] 2.1 In `apps/desktop/src/app/chat/composer/composer-utils.ts`, replace only the Space branch of `acceptsTriggerCompletion` with the `design.md` D1 rule (`slashArgStage(query) || activeExplicit` after the existing slash / non-empty / not-free-text guards). Update its doc comment to describe the command-name rule. Do not edit `index.tsx`, `use-composer-trigger.ts`, `composer-focus-keys.ts`, or any sidebar file. Proof: `cd O:/workspaces/.worktrees/wt-fix-composer-space/apps/desktop && npx vitest run src/app/chat/composer/composer-utils.test.ts src/app/chat/composer/slash-space-accept.test.tsx` exits 0.
- [x] 2.2 Scope check. Proof: `git -C O:/workspaces/.worktrees/wt-fix-composer-space diff --name-only main` lists exactly `apps/desktop/src/app/chat/composer/composer-utils.ts`, `apps/desktop/src/app/chat/composer/composer-utils.test.ts`, `apps/desktop/src/app/chat/composer/slash-space-accept.test.tsx`, and this change's `openspec/` files (if committed on the branch).

## 3. Regression guards (no edits)

- [x] 3.1 Trigger hook and popover behavior are unchanged. Proof: `cd O:/workspaces/.worktrees/wt-fix-composer-space/apps/desktop && npx vitest run src/app/chat/composer/hooks/use-composer-trigger.test.ts src/app/chat/composer/at-folder-navigation.test.tsx src/app/chat/composer/trigger-popover.test.tsx` exits 0.
- [x] 3.2 Focused-control Space ownership is preserved (spec requirement "Focused controls keep keyboard ownership of Space"). `composer-focus-keys.test.ts` is unmodified, and its `refuses editables; leaves Enter and Space to buttons but allows / and typing` case passes. Proof: `cd O:/workspaces/.worktrees/wt-fix-composer-space/apps/desktop && npx vitest run src/lib/keybinds/composer-focus-keys.test.ts` exits 0, and `git -C O:/workspaces/.worktrees/wt-fix-composer-space diff --quiet main -- apps/desktop/src/lib/keybinds/` exits 0.
- [x] 3.3 Keyboard drag accessibility is preserved (`design.md` §1 rows C–D, D4): Space on the session-row grabber still arms a drag, and Space on the row's ⋯ control is not prevented. Proof: `cd O:/workspaces/.worktrees/wt-fix-composer-space/apps/desktop && npx vitest run src/app/chat/sidebar/session-row.test.tsx` exits 0.
- [x] 3.4 Full composer suite plus static gates. Proof: `cd O:/workspaces/.worktrees/wt-fix-composer-space/apps/desktop && npx vitest run src/app/chat/composer/ && npm run typecheck && npx eslint src/app/chat/composer/composer-utils.ts src/app/chat/composer/composer-utils.test.ts src/app/chat/composer/slash-space-accept.test.tsx` exits 0.

## 4. Artifact gate

- [x] 4.1 Strict OpenSpec validation with the canonical executable. Proof: `cd O:/workspaces/oss/hermes-agent && openspec validate fix-desktop-composer-space-interception --strict` exits 0.
- [ ] 4.2 Independent review (@reviewer) of the worktree diff against this change's spec. The reviewer re-runs tasks 2.1, 2.2 and 3.1–3.4 and records findings under `reviews/`. No merge to `main` without Commander approval.
