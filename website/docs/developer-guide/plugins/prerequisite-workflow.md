# Native proposal workflow selection

The authenticated Gateway command `/openspec-propose <change-id>` can select an operator-managed `prerequisite_catalog`. The command must resolve to the installed, enabled skill and take exactly one lowercase hyphen-separated identifier. Only the original command accepted at ingress can select a catalog. Hook and alias rewrites, model tool arguments, ordinary chat text, stacked skill commands, and recovered transcripts cannot create that selection.

Selection is not authorization to read. The Gateway binds the selection to the admitted turn. Its FIFO handoff is process-local, one-use, and expires after 120 seconds. DB-only recovery and in-band follow-ups do not inherit it. A native child intersects the parent declaration with its own served-profile catalog for the same planning home and change. Its host-selected local working directory must match that planning home; an unknown workspace or a separate worktree does not implicitly alias it. Required pins must agree exactly, and the lower operation and time limits apply.

## Saved configuration

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

Missing, incompatible, or unreadable current profile catalogs do not change ordinary tool controls. Catalog selection does not fall back to an older profile configuration or ignore an unreadable or malformed applicable managed layer. Selection failures use bounded reason codes in logs. Renew pins through operator configuration for new assignments only; existing assignments do not reload their selection on resume.
