# Independent Code Review — Desktop Sidebar Collapsible Tools

- Task: `t_5ea373f2`
- Change: `desktop-sidebar-collapsible-tools`
- Candidate HEAD: `0d4d58e777ac4b72f8c6274e8bab59511dcfa437`
- Verdict: **REQUEST CHANGES / PROOF GATE FAILED**
- Review scope: sidebar source/tests, active OpenSpec delta, Desktop architecture/design guidance, exact desktop proof command

## Acceptance audit

| Criterion | Result | Evidence |
| --- | --- | --- |
| New Session remains pinned above daily navigation | PASS | `apps/desktop/src/app/chat/sidebar/index.tsx:1584` renders the New Session-only navigation before the scrollable session area. |
| Sessions/Projects, Pinned, and Search precede TOOLS | PASS | `apps/desktop/src/app/chat/sidebar/index.tsx:1945`; focused ordering coverage in `chat-sidebar.integration.test.tsx:191-253`. |
| TOOLS is collapsed by default and exposes utility routes | PASS | `navigation.tsx:181-206`; disclosure and route coverage in `navigation.test.tsx:43-91` and `chat-sidebar.integration.test.tsx:255-279`. |
| Existing routing, session resume, and new-session behavior remain covered | PASS (focused) | Focused sidebar command passed 26/26; relevant tests at `chat-sidebar.integration.test.tsx:255-299`. |
| Profile/session state has no regression | FAIL | `use-profile-rail-refresh-on-active.test.ts:117-135` fails independently at line 122. |
| Exact desktop suite exits 0 | FAIL | Exact proof command exited 1: 30 failed files, 1,540 passed files, 13 skipped; 50 failed tests, 14,175 passed, 132 skipped; one unhandled error. |

## Findings

### [P1] Mandatory desktop proof command exits 1

The required command:

`npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run test`

completed with exit code 1 after 926.81 seconds. Vitest reported 30 failed test files, 50 failed tests, and one unhandled rejection. This violates OpenSpec task 1.5 and the card's explicit all-tests-pass acceptance gate. Representative additional failures include Electron packaging/signing/platform fixtures and hoisted store mocks; these prevent a clean suite verdict regardless of whether they predate the sidebar diff.

Required remediation:

1. Establish the repository-supported proof environment and resolve or isolate every failure through the project’s accepted test configuration—not by weakening the proof command.
2. Re-run the exact command to exit code 0.
3. Keep OpenSpec task 1.5 unchecked until that succeeds.

### [P1] Signed-out profile refresh contract is internally inconsistent and fails standalone

Standalone reproduction:

`npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run test -- src/app/chat/sidebar/use-profile-rail-refresh-on-active.test.ts`

Result: exit code 1; 1 failed / 8 passed. The failing scenario marks `https://gateway.example.com` signed out at `use-profile-rail-refresh-on-active.test.ts:118`, but does not assign that URL to the active `$connection`. Production code intentionally evaluates `isTerminalSignedOut(connection?.baseUrl)` at `use-profile-rail-refresh-on-active.ts:30-32`, so mount refreshes and the assertion at line 122 fails.

Required remediation:

1. Make the fixture model an active connection whose `baseUrl` is the signed-out URL, and reset that state during teardown; or, if the intended contract is URL-independent, change the production contract and its broader tests explicitly.
2. Verify signed-out suppression and sign-in recovery both pass standalone.
3. Re-run the full proof command to exit code 0.

## Passing evidence

- `npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run test -- src/app/chat/sidebar/navigation.test.tsx src/app/chat/sidebar/chat-sidebar.integration.test.tsx` — exit 0; 2 files, 26/26 tests passed.
- `npm --prefix O:/workspaces/oss/hermes-agent/apps/desktop run typecheck` — exit 0 across all configured TypeScript checks.
- `git diff --check -- apps/desktop/src/app/chat/sidebar` — exit 0.

## Scope and governance notes

- No implementation files were edited during review.
- The repository does not contain the mandated named files `code-review-mandate/spec.md` or a project `lessons-learned.md`; review used the loaded `openspec-verifier` mandate, active change artifacts, and authorized Desktop architecture sources.
- OpenSpec task 1.5 remains correctly unchecked.
