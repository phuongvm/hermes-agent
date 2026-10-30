# Exploration: `build-desktop-installer.ps1` Failure at `assert-root-install.mjs` (`build.mjs:12`)

> **Date**: 2026-10-30
> **Active Project**: `hermes-agent` (`O:/workspaces/oss/hermes-agent`)
> **Status**: Empirical Root Cause Verified — Ready for `/crew-no-spec` Card Dispatch

---

## 1. Problem Statement

When Commander restarted Hermes and executed `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1`, the script failed at line 203 (`& npm.cmd --prefix apps/desktop run build`):
```text
Error: Command failed: C:\Users\phuong\AppData\Local\nvm\v22.22.0\node.exe O:\workspaces\oss\hermes-agent\apps\desktop\scripts\assert-root-install.mjs
    at step (file:///O:/workspaces/oss/hermes-agent/apps/desktop/scripts/build.mjs:12:39)
    at buildSourceDesktop (file:///O:/workspaces/oss/hermes-agent/apps/desktop/scripts/build.mjs:13:3)
...
Exception: O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1:203
Desktop build failed with exit code 1
```

---

## 2. Empirical Root Cause (`diagnosing-bugs` Forensic & Repro Proof)

### 2.1 Four-Step Failure Chain (Proven from Live npm Logs & Ephemeral Repro)

1. **Why `apps/desktop` Dependencies Were Missing After Restart (`hermes_cli/source_build.py:92-100`)**:
   - On Hermes restart/update, `prepare_launch_dependencies()` runs `scripts/build/node-deps.mjs --source O:\workspaces\oss\hermes-agent --reuse --workspace ui-tui --workspace web` unless `apps/desktop/dist/index.html` or `apps/desktop/release/win-unpacked/Hermes.exe` already exists (note `build-desktop-installer.ps1` builds into `release/win-nsis-build/`, not `release/win-unpacked/`).
   - Running `npm ci --workspace ui-tui --workspace web` (or pulling new `apps/desktop/package.json` deps during daily upstream sync) leaves `node_modules` missing `apps/desktop`-only packages (`electron`, `electron-builder`, `katex`, etc.), causing `assert-root-install.mjs` (`build-desktop-installer.ps1:193`) to exit `1`.

2. **Primary Defect — Hardcoded Stale Node 22 in `build-desktop-installer.ps1:67`**:
   - Lines 65–71 hardcode:
     ```powershell
     $NodeDir = "C:\Users\phuong\AppData\Local\nvm\v22.22.0"
     if (Test-Path $NodeDir) { $env:PATH = "$NodeDir;$env:PATH" }
     ```
   - Upstream `O:\workspaces\oss\hermes-agent\package.json` (lines 74–77), `apps/desktop/package.json` (line 15), and `.npmrc` (`engine-strict=true`) now require:
     `{"node": ">=26", "npm": "<11.10.0 || >=11.17.0"}`.
   - Meanwhile, Hermes PM has already provisioned the exact required toolchain under `O:\workspaces\_config\agent4070\hermes\tools\`:
     - `node-26.12.1-win32-x64\node.exe` (`v26.12.1`)
     - `npm-12.0.2-win32-x64\npm.cmd` (`12.0.2`)
     - `git-2.53.0+3-win32-x64\cmd\git.exe`

3. **Secondary Defect — Silent Error Swallowing & Wrong Install Command (`build-desktop-installer.ps1:194-197`)**:
   - Forensic npm log `C:\Users\phuong\AppData\Local\npm-cache\_logs\2026-10-30T05_48_34_521Z-debug-0.log` captured the exact crash of line 196 (`& npm.cmd run install:desktop` → `npm install --workspace apps/desktop`):
     ```text
     18 error code EBADENGINE
     19 error engine Unsupported engine
     20 error engine Not compatible with your version of node/npm: hermes-agent@1.0.0
     21 error notsup Required: {"node":">=26","npm":"<11.10.0 || >=11.17.0"}
     21 error notsup Actual:   {"npm":"10.9.4","node":"v22.22.0"}
     28 verbose exit 1
     ```
   - Ephemeral `< 3s` reproduction harness in `O:\workspaces\_config\agent4070\hermes\cache\scratch` (cleaned up immediately after run: `probe_dir cleaned: True`) reproduced `NODE22 install:desktop rc= 1` (`EBADENGINE`) deterministically.
   - Because line 196 did **not** check `$LASTEXITCODE`, `build-desktop-installer.ps1` ignored the `EBADENGINE` failure and proceeded to line 201 (`& npm.cmd --prefix apps/desktop run build`, logged at `05_49_02_639Z`), which re-ran `assert-root-install.mjs` inside `build.mjs:12:39` and threw at line 203!
   - Furthermore, `npm run install:desktop` (`npm install --workspace apps/desktop`) is architecturally wrong on a production checkout: `npm install` can mutate `package-lock.json` (marking `install-stamp.json` `[DIRTY]`), prunes `ui-tui` and `web` packages from shared `node_modules`, and skips `prepareElectronRuntime` (`vcruntime140.dll`) + `node_modules/.hermes-node-deps`.

---

## 3. Solution & Deep Module Interface

Confine all changes strictly to `O:\workspaces\_config\agent4070\hermes\services\build-desktop-installer.ps1` (plus Seam B tests in `O:\workspaces\_config\agent4070\hermes\services\tests\test_synology_release_publisher.py`), keeping `0` diff in `O:\workspaces\oss\hermes-agent\apps\desktop\` and leaving `hermes-env.cmd` untouched so running Gateway/Dashboard/Buzz launchers experience zero side effects:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ build-desktop-installer.ps1 Toolchain & Workspace Preparation Ladder         │
├──────────────────────────────────────────────────────────────────────────────┤
│ Rung 1 (Explicit Test/Operator Override):                                    │
│   -ToolchainBinDir <dir> (or hermetic sandbox when hermes-env.cmd is absent) │
│ Rung 2 (Canonical Hermes PM Resolver via TargetRoot Python):                 │
│   hermes_cli.source_build.source_build_env(explicit=False, verify=False)     │
│   + pm.ensure("git") -> injects PM node >=26, npm >=12, git,                 │
│   HERMES_PYTHON, PYTHON, HERMES_VC_RUNTIME (JSON key subset, zero secrets)   │
│ Rung 3 (Direct <HermesHome>\tools\ Semantic-Version Fallback):               │
│   <HermesHome>\tools\npm-*-win32-* + node-*-win32-* + git-*-win32-*\cmd      │
│   sorted by parsed [version] descending (never hardcoded v22.22.0!)          │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ Workspace Dependency Guard (before `npm --prefix apps/desktop run build`)    │
├──────────────────────────────────────────────────────────────────────────────┤
│ 1. Run `node apps/desktop/scripts/assert-root-install.mjs`                   │
│ 2. On non-zero exit:                                                         │
│    - If `scripts/build/node-deps.mjs` exists:                                │
│      `node scripts/build/node-deps.mjs --source $TargetRoot --reuse`         │
│      with all present workspaces (`ui-tui`, `web`, `apps/desktop`)           │
│      -> locked `npm ci`, keeps TUI+Web+Desktop intact, stages Electron       │
│         runtime + vcruntime140.dll, writes `.hermes-node-deps`, 0 git diff!  │
│    - Else fallback: `npm.cmd ci` in `$TargetRoot`                            │
│    - FAIL CLOSED if install exits non-zero (`throw` immediately)             │
│    - Re-run `node assert-root-install.mjs` and FAIL CLOSED if still non-zero │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Public Testing Seams

- **Seam B (`pwsh.exe -NoProfile -File build-desktop-installer.ps1` in `test_synology_release_publisher.py`)**:
  1. **PM `<HermesHome>\tools\` Toolchain Selection over Stale Node 22**:
     - Sandbox with `hermes-env.cmd` present and `<HermesHome>/tools/node-26.7.0-win32-x64`, `node-26.12.1-win32-x64`, `npm-12.0.2-win32-x64` selects `node-26.12.1` + `npm-12.0.2` (semantic version order `26.12.1 > 26.7.0`) and never prepends hardcoded `nvm\v22.22.0`.
  2. **`assert-root-install.mjs` Failure Triggers `scripts/build/node-deps.mjs` Union Install (`ui-tui`, `web`, `apps/desktop`)**:
     - When fake `assert-root-install.mjs` exits `1` until `node-deps.mjs` writes marker, `build-desktop-installer.ps1` invokes `node scripts/build/node-deps.mjs --source <root> --reuse --workspace ui-tui --workspace web --workspace apps/desktop` (never `npm run install:desktop`), re-verifies `assert-root-install.mjs` (`rc=0`), and completes packaging + Synology upload.
  3. **Fail-Closed Propagation When Dependency Install Fails (`EBADENGINE` / Network Error)**:
     - When `assert-root-install.mjs` exits `1` and `node-deps.mjs` (or fallback `npm ci`) exits non-zero, `build-desktop-installer.ps1` aborts immediately with a clear `"Desktop dependency installation failed"` error BEFORE invoking `npm --prefix apps/desktop run build` or `electron-builder` or contacting Synology.
  4. **Existing 228 Seam A + Seam B + Vaultwarden Tests**:
     - Remain 100% green (`rc=0`).

---

## 5. Out of Scope

- Modifying `O:\workspaces\_config\agent4070\hermes\services\hermes-env.cmd` (shared by live 24/7 Gateway, Dashboard, and Desktop launchers; can be evaluated separately if desired).
- Modifying any file under `O:\workspaces\oss\hermes-agent\apps\desktop\`.
