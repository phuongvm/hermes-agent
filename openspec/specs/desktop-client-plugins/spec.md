# Specification: Always-Available Bundled Desktop Client Plugins Specification

## Purpose

Defines the client-side availability, package pairing, controlled local disk-override precedence, graceful backend-absent degradation, and deterministic sync/integrity verification contract for organizational desktop plugins (`crew`, `openspec`, `radar`, `redline`) bundled into the Hermes Desktop renderer.

## Requirements

### Requirement: Bundled organizational desktop plugins ship always-available in the renderer
The Hermes Desktop renderer SHALL bundle the plain-ESM desktop entrypoints for `crew`, `openspec`, `radar`, and `redline` under `apps/desktop/src/plugins/<id>/plugin.js` and discover them at boot via the bundled plugin loader so they remain available regardless of whether the connected gateway is local, remote, OAuth-protected, profile-switched, or offline. Bundled plugin assets MUST exclude standalone non-plugin browser bundles such as `crew/desktop/app.js`.

#### Scenario: Cold boot connected to a remote gateway without local plugins
- **WHEN** Hermes Desktop starts on a machine with an empty `<HERMES_HOME>/desktop-plugins` directory and connects to a remote gateway that has `crew`, `openspec`, `radar`, and `redline` backend halves enabled
- **THEN** all four plugins (`crew`, `openspec`, `radar`, `redline`) MUST register at boot with `kind: 'bundled'` and `status: 'loaded'` unless explicitly disabled by the user
- **AND** no remote HTTP fetch to `/api/desktop-plugins/*` MUST be required for those four plugins to load

#### Scenario: User explicitly disables a bundled organizational plugin
- **WHEN** the user toggles off a bundled organizational plugin (for example `radar`) in Capabilities ▸ Plugins and restarts Hermes Desktop
- **THEN** the persisted user decision MUST be honored on subsequent boots
- **AND** the plugin MUST remain inventoried with `kind: 'bundled'` and `status: 'disabled'` without activating its contributions

#### Scenario: Connected gateway lacks a plugin's backend half
- **WHEN** Hermes Desktop connects to a gateway or profile where one or more of the four backend halves are absent or offline
- **THEN** the bundled desktop plugin MUST remain loaded without throwing uncaught exceptions or spamming notification toasts
- **AND** its status-bar chip and page surfaces MUST degrade cleanly using their built-in empty/unavailable UI states

#### Scenario: Excluded standalone browser bundles are not shipped
- **WHEN** the bundled organizational plugins are synced and compiled into Hermes Desktop
- **THEN** `apps/desktop/src/plugins/crew/` MUST contain `plugin.js` and MUST NOT contain `app.js`

### Requirement: Scoped unified-package pairing without affecting standalone bundled plugins
The bundled plugin discovery loader SHALL populate `packageName` on a bundled `PluginRecord` if and only if the plugin export explicitly declares `packageName` or its `id` is registered in `apps/desktop/src/plugins/org-plugins.manifest.json`. Upstream standalone bundled plugins (`hermes-bots`, `kanban`, `radio`) MUST NOT receive a synthetic `packageName`.

#### Scenario: Bundled organizational plugin pairs with remote or local agent row
- **WHEN** `mergePluginPackages()` merges bundled `PluginRecord` entries (`crew`, `openspec`, `radar`, `redline`) with backend `AgentPluginRow` entries of matching package names
- **THEN** each matched package MUST merge into exactly one `PluginPackage` row with `kind: 'both'`, `desktopMissing: false`, and `agentMissingInProfile: false`
- **AND** Capabilities ▸ Plugins MUST NOT display a duplicate `desktop:<id>` row alongside the unified row

#### Scenario: Bundled organizational plugin when agent half is absent from selected profile
- **WHEN** `mergePluginPackages()` runs while the selected profile has no matching `AgentPluginRow` for a bundled organizational plugin
- **THEN** the plugin MUST appear as a single row with `kind: 'both'`, `desktop` populated, `agent: null`, and `agentMissingInProfile: true`
- **AND** the row MUST NOT offer a destructive desktop Uninstall button

#### Scenario: Upstream standalone bundled plugins remain unaffected
- **WHEN** `discoverBundledPlugins()` registers `hermes-bots`, `kanban`, and `radio`
- **THEN** their `PluginRecord.packageName` MUST remain `undefined`
- **AND** `mergePluginPackages()` MUST continue emitting them as standalone `kind: 'desktop'` rows keyed by `desktop:<id>`

### Requirement: Controlled 3-way disk-override and deduplication precedence
When the runtime disk loader evaluates an on-disk plugin whose `id` matches an already-registered `kind: 'bundled'` plugin, it SHALL classify the collision deterministically into exactly one of three outcomes: `override` (suspend bundled and activate disk copy), `dedup-bundled` (keep bundled active without creating a shadowed row), or `shadow-stale` (keep bundled active and publish a visible disabled `:disk-shadowed` row).

#### Scenario: Linked developer checkout overrides bundled copy with live reload and restore
- **WHEN** an on-disk unified package folder carries `.hermes-package.json` with `linked: true` and a non-empty `source` matching a bundled plugin `id`
- **THEN** the loader MUST suspend and deactivate the bundled plugin's registrations before activating the disk copy so contributions, styles, and listeners do not duplicate
- **AND** the effective default-enabled posture MUST preserve the bundled plugin's default unless the user explicitly disabled the plugin in persisted decisions
- **AND** when the linked disk folder is subsequently removed or retired, the loader MUST restore and re-activate the suspended bundled plugin automatically without requiring an app restart

#### Scenario: Newer non-linked local unified package copy overrides bundled copy
- **WHEN** a non-linked on-disk unified package folder carries `.hermes-package.json` whose `package` matches the bundled record's `packageName`, its `plugin.js` SHA-256 differs from the manifest's `sha256`, and its `sourceMtimeMs` is strictly greater than the manifest's `syncedAtMs`
- **THEN** the loader MUST suspend the bundled plugin and activate the newer disk copy
- **AND** retiring the disk folder MUST restore the bundled plugin cleanly

#### Scenario: Identical or older local unified package copy deduplicates cleanly without stale-copy noise
- **WHEN** a non-linked on-disk unified package folder carries `.hermes-package.json` whose `package` matches the bundled record's `packageName`, and either its `plugin.js` SHA-256 equals the manifest's `sha256` or its `sourceMtimeMs` is less than or equal to the manifest's `syncedAtMs`
- **THEN** the bundled plugin MUST remain the sole active registration
- **AND** the loader MUST return `null` and MUST NOT publish a `${id}:disk-shadowed` inventory row

#### Scenario: Standalone unmatched disk copy preserves upstream disk-shadowed invariant
- **WHEN** an on-disk plugin folder without `linked: true` and without a matching unified `packageName` claims the `id` of any bundled plugin (such as `hermes-bots` or a hand-copied standalone `crew` folder)
- **THEN** the bundled plugin MUST remain active
- **AND** the loader MUST return `null` and MUST publish `${id}:disk-shadowed` with `kind: 'disk'`, `status: 'disabled'`, and `file` set to the on-disk path

### Requirement: Deterministic plugin sync and offline integrity verification
The repository SHALL provide a zero-dependency Node ESM build script (`scripts/build/sync-desktop-plugins.mjs`) that stages all four organizational `plugin.js` bundles from their canonical sources into `apps/desktop/src/plugins/<id>/plugin.js`, updates `apps/desktop/src/plugins/org-plugins.manifest.json`, and supports a read-only offline `--check` verification mode without mutating the git working tree during normal renderer builds.

#### Scenario: Sync stages byte-identical bundles and updates manifest
- **WHEN** an operator runs `node scripts/build/sync-desktop-plugins.mjs` with accessible source repositories (or explicit `--plugins-root`)
- **THEN** each of `crew`, `openspec`, `radar`, and `redline` MUST be copied byte-for-byte into `apps/desktop/src/plugins/<id>/plugin.js`
- **AND** each copied file MUST be verified to import only allowlisted specifiers (`@hermes/plugin-sdk`, `react`, `react/jsx-runtime`, `react/jsx-dev-runtime`)
- **AND** `apps/desktop/src/plugins/org-plugins.manifest.json` MUST record the exact lowercase hex SHA-256 and `syncedAtMs` for every bundled plugin

#### Scenario: Offline integrity check detects drift, invalid imports, or extraneous files
- **WHEN** `node scripts/build/sync-desktop-plugins.mjs --check` runs in a clean checkout with no sibling plugin repositories present
- **THEN** it MUST exit `0` if and only if all four `plugin.js` files exist, match their recorded SHA-256 digests in `org-plugins.manifest.json`, contain zero disallowed import specifiers, and `apps/desktop/src/plugins/crew/app.js` does not exist
- **AND** any missing file, digest mismatch, disallowed import specifier, or extraneous `app.js` MUST cause `--check` to exit non-zero with a clear diagnostic message

#### Scenario: Renderer build remains clean-tree hermetic
- **WHEN** `pnpm --filter hermes run build` executes in a clean git checkout
- **THEN** Vite MUST bundle all four organizational plugins from the committed `apps/desktop/src/plugins/*/plugin.js` tree without running the sync writer or dirtying any tracked files
