# Exploration: Online Meeting Review Context Disconnect

## The Problem
Webhook pipeline is ephemeral.
1. Webhook fires -> Generates summary -> Saves to DB -> Sends Matrix draft -> Process dies.
2. Human replies "Yes" or "Edit X" in Matrix later.
3. Matrix conversational agent reads reply. Agent lacks connection to the dead pipeline. Doesn't know how to update the internal DB or trigger Teams delivery.

## Visualizing the Disconnect

```text
┌────────────────────┐          ┌────────────────────┐
│ Pipeline Daemon    │          │ Matrix Agent       │
│ (Runs & Dies)      │          │ (Always Listening) │
├────────────────────┤          ├────────────────────┤
│ 1. Extract VTT     │          │                    │
│ 2. Generate JSON   │          │                    │
│ 3. Save Pending DB │          │                    │
│ 4. Send Matrix msg -> [Gap] ->│ 5. Read Human msg  │
└────────────────────┘          │ 6. ??? How deliver?│
                                └────────────────────┘
```

## Explored Paths to Bridge the Gap

### Path 1: CLI Commands and Agent Skill (Hermes Native)
- **Mechanism**: Pipeline embeds job_id in Matrix message.
- **Agent Side**: Build teams-meeting-review-workflow skill. Agent extracts job_id from chat context.
- **Execution**: Agent runs new CLI commands (hermes teams-pipeline update id and hermes teams-pipeline deliver id) to mutate DB and push to Teams.
- *Pros*: Uses existing CLI pattern. Easy to audit.
- *Cons*: Agent must correctly format CLI arguments and handle temporary files for payload updates.

### Path 2: Local HTTP API Gateway
- **Mechanism**: Build local REST endpoint in Hermes Gateway for pipeline job management.
- **Agent Side**: Agent sends HTTP POST to approve/edit.
- *Pros*: Cleaner programmatic interface.
- *Cons*: Requires expanding Gateway API surface just for Teams.

## Risks and Unknowns
1. **Stale Job Reaper**: Pipeline resets stuck jobs after N minutes. pending_review jobs might get wiped if human takes hours to review. Must whitelist this state.
2. **Schema Drift**: If human asks agent to edit summary, agent might break the strict JSON template schema. Agent must verify schema before calling deliver.
3. **Channel Routing**: Pipeline knows default channel. Human might say "Send to Engineering". Agent needs way to override destination during delivery.

## Conclusion
Exploration crystallized. Path 1 (CLI and Agent Skill) aligns best with current architecture. No code changes needed yet. Ready to exit Explore phase.
