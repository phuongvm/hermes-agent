# Native proposal workflow selection

The authenticated Gateway command `/openspec-propose <change-id>` can select an operator-managed `prerequisite_catalog`. The command must resolve to the installed, enabled skill and take exactly one lowercase hyphen-separated identifier. Only the original command accepted at ingress can select a catalog. Hook and alias rewrites, model tool arguments, ordinary chat text, stacked skill commands, and recovered transcripts cannot create that selection.

Selection is not authorization to read. The Gateway binds the selection to the admitted turn. Its FIFO handoff is process-local, one-use, and expires after 120 seconds. DB-only recovery and in-band follow-ups do not inherit it. A native child intersects the parent declaration with its own served-profile catalog for the same planning home and change. Its host-selected local working directory must match that planning home, including path casing after syntax normalization. An unknown workspace, a differently cased path, or a separate worktree does not implicitly alias it. Required pins must agree exactly, and the lower operation and time limits apply.

## Operation descriptors

For an active native assignment, the matcher accepts only exact catalog resources and validated parameters: `read_file` with a pinned absolute path and bounded pagination; `skill_view` with a pinned skill and, when requested, an explicitly pinned reference; or one of `openspec_status`, `openspec_instructions`, `openspec_spec_show`, and `openspec_spec_list` scoped to the declared planning home and change or baseline. Generic wrappers, terminal commands, code execution, network tools, and broad search do not match. A match is metadata only. It neither reserves an attempt nor opens a resource; downstream dispatch must still check adapter support, current authority, and content integrity.

## Progress supervision hook

An in-process plugin can feature-detect `ctx.reserve_prerequisite_read` when it registers its `pre_tool_call` callback. Call it with `session_id`, `assignment_id`, `tool_call_id`, and `tool_name` from the active assignment and hook. The host checks these against the current executor call and matches its own original arguments; the plugin does not supply read arguments, a path override, or an approval decision. A call outside that scope returns `unavailable`.

Only `reserved` means a new accounting attempt was recorded. A progress supervisor may decline its own local progress veto in that case. It must retain its veto when the host lacks the method or returns any other disposition. This is not authorization: other controls and native approval still apply, and no ordinary reader, document extractor, directory scanner, or CLI subprocess may handle a reserved operation in place of a verified bounded adapter. If an unsupported tool or backend reaches guarded dispatch, the host refuses dispatch with a bounded `prerequisite_*` error (`prerequisite_reader_unavailable`, `prerequisite_unsupported_backend`, etc.). Replays, vetoes, and cleanup failures never refund a consumed attempt.

## Verified-handle capture, cache accounting, and supported backends

Every admitted operation (`read_file`, `skill_view`, `openspec_status`, `openspec_instructions`, `openspec_spec_show`, and `openspec_spec_list`) materializes its declared catalog pins through `agent.prerequisite.reader` and `agent.prerequisite.adapters`:

- **Hard ceilings**: `1 MiB` per resource (`MAX_FILE_BYTES`), `16 MiB` aggregate physical source bytes per assignment (`MAX_SOURCE_BYTES`), `128` catalog resources (`MAX_RESOURCES`), and `256 KiB` returned UTF-8 text per operation (`MAX_OUTPUT_BYTES`). Profile settings (`file_read_max_chars`, `tool_output.max_bytes`) may lower the returned output ceiling below `256 KiB`, never raise it.
- **Same-handle capture**: Before reading content bytes, the reader reserves each uncached pin's full `max_file_bytes` atomically under the assignment lock. On Windows, it rejects NT-namespace, device, and UNC paths before I/O, verifies ancestor directories have no reparse attributes, opens the target once with `FILE_SHARE_READ` (denying write/delete sharing during capture) and `FILE_FLAG_OPEN_REPARSE_POINT`, verifies `GetFileType == FILE_TYPE_DISK` and `GetFinalPathNameByHandleW`, checks `(st_dev, st_ino, st_size)` before reading, streams at most `max_file_bytes` without an extra EOF probe, re-verifies handle metadata and pinned `sha256`, and closes every handle in `finally`. On POSIX, it traverses from `/` via `dir_fd` with `O_DIRECTORY | O_NOFOLLOW` and opens the leaf with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK`.
- **Frozen-buffer cache and eviction**: Captured bytes are cached per assignment (`cached_buffers`). Subsequent `read_file` pages or cross-adapter reads of an already-captured pin (for example, `skill_view(name, file_path)` reusing `SKILL.md` or `openspec_spec_show` reusing a baseline captured by `openspec_spec_list`) do not reopen or rescan disk, while still consuming one operation slot. Evicting cached entries (`evict_cached_resources`) never refunds `source_bytes_reserved`; a later cache miss must reserve `max_file_bytes` again against the remaining `16 MiB` aggregate budget.
- **Adapter isolation**: `skill_view` derives `linked_files` solely from pinned `reference/<skill>/*` catalog entries, expands only in-memory template variables (`${HERMES_SKILL_DIR}`, `${HERMES_SESSION_ID}`), never executes inline `!`...`` shell snippets, and rejects skills declaring external `deps`. OpenSpec read adapters serve only pinned `snapshot/*` and `baseline/*` buffers, never spawning the `openspec` CLI, template fallbacks, or `openspec/specs/` directory scans. Hermes read-policy denylists and `redact_sensitive_text` remain active on all returned content.
- **Supported backends**: Only local host filesystems (`hosted_scope() is None` and local terminal file backend) are eligible. Tenant `hosted_scope` sandboxes and remote or container backends (`ssh`, `docker`, `modal`, `daytona`, etc.) fail closed with `prerequisite_unsupported_backend` and zero host opens or fallback reads.

## Safe config refresh and saved configuration

The following fragment is intentionally incomplete, with placeholder paths and identity values. Do not install it as-is. No profile or plugin is enabled by this example.

```yaml
prerequisite_catalog:
  openspec-propose:
    example-topic:
      planning_home: /srv/planning/example
      operations: 64
      seconds: 600
      resources:
        governance/AGENTS.md:
          path: /srv/governance/AGENTS.md
          device: 0
          inode: 1
          size: 1024
          max_file_bytes: 2048
          sha256: '<reviewed-sha256>'
        project/requirements: null
        project/architecture: null
```

A complete entry includes:

- All nine governance documents: `AGENTS.md`, `shared/startup-rules.md`, `shared/safety.md`, `skills/task-verification-mandate.md`, `shared/rules-index.md`, `user/communication-language.md`, `shared/lessons-digest.md`, `skills/anti-deception-instruction.md`, and `skills/compliance-enforcer.md`, plus `workflow/plan-execution-standards.md` and `guides/ai-native-documentation.md`, using `governance/<path>` keys.
- `skill/<name>` pins for `verification-before-claim`, `openspec-propose`, `openspec-workflow-governance`, `codebase-design`, `deep-reference-protocol`, `ai-agent-collaboration`, `hermes-agent`, and `hermes-boot-protocol`. Pin both `reference/verification-before-claim/references/completion-gate.md` and `reference/openspec-workflow-governance/references/openspec-agent-workflow-guide.md`, plus any other explicitly selected references relative to the corresponding pinned skill directory. There is no recursive discovery.
- `project/agent-share`, `project/exploration`, and `project/receipt`, with paths relative to the planning home. Use `openspec/workspace/sessions/agent_share.md` and operator-selected Markdown documents under `openspec/workspace/explorations/`. Declare `project/requirements` and `project/architecture` as either pinned text documents at explicit paths relative to the planning home (for example, `docs/REQUIREMENTS.md`) or `null` for explicit absence. They are never discovered implicitly. A pin on one profile and `null` on the other excludes the document; conflicting present pins reject the entire selection.
- `baseline/nerve-crew-dod-and-role-handoff` and `baseline/nerve-reflex-resilience`, each bound to `openspec/specs/<id>/spec.md`. The required descriptors are `snapshot/status` and one `snapshot/instructions/<artifact>` for each of `proposal`, `design`, `specs`, and `tasks` under `openspec/workspace/prerequisites/<change-id>/`.

`hermes config check` validates the same effective catalog used at selection, including managed-only declarations, without echoing raw values or opening resource contents. Managed catalog values take precedence, including explicit `null`; a managed pin replaces the whole user pin, rather than combining identity fields across layers. At selection, local metadata checks reject missing files, symlinks or reparses, and mismatched device, file ID, or size. On Windows, preflight also requires a fixed drive and refuses UNC paths. On POSIX, preflight does not establish mount type: provision catalogs only on known local filesystems, not on network or virtual mounts. These are preflight checks, not an attestation of content. An eventual bounded reader must independently verify the opened handle and pinned SHA-256 after guards permit dispatch. In particular, an equal-size edit can pass metadata checks but must not pass content verification.

Missing, incompatible, or unreadable current profile catalogs do not change ordinary tool controls. Catalog selection does not fall back to an older profile configuration or ignore an unreadable or malformed applicable managed layer, including a dangling policy link. Selection failures use bounded reason codes in logs. Renew pins through operator configuration for new assignments only; existing assignments do not reload their selection on resume.
