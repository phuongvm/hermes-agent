## Why

The automated online meeting pipeline (`teams_pipeline` webhook processor) currently bypasses review gates entirely, delivering generated summaries directly to Teams channels. This auto-delivery behavior poses reliability and compliance risks. We need parity with the offline pipeline (which uses a master orchestrator skill with a Review Gate), ensuring online push notifications hold generated summaries in a pending state awaiting explicit Commander approval before delivery.

## What Changes

- Modify the webhook processor logic (`plugins/teams_pipeline/pipeline.py`) to intercept the final delivery phase (`_write_sinks`).
- Introduce a `require_review` flag or state mechanism to pause the job execution.
- Expose pending jobs for review in the Matrix chat interface.
- Add CLI commands (`plugins/teams_pipeline/cli.py`) or API endpoints to allow explicit delivery or rejection of approved/rejected pending jobs.
- Update relevant configuration files to support the `require_review` setting and Matrix notification mapping.

## Capabilities

### New Capabilities
- `teams-webhook-review-gate`: Introduces a Human-in-the-Loop review mechanism for automated online meeting summary generation via webhooks, pausing delivery until explicit approval is granted.

### Modified Capabilities
- `teams-connector`: Behavior change in the pipeline where the automated end-to-end processing is intercepted, and final output delivery is no longer automatic by default for webhook triggers.

## Impact

- `plugins/teams_pipeline/pipeline.py`: Modifications to intercept `_write_sinks()` and manage job state (`pending_review`).
- `plugins/teams_pipeline/cli.py`: Addition of commands for handling job approval and final delivery.
- `plugins/platforms/teams/adapter.py`: Potential adjustments to `TeamsSummaryWriter` handling.
- `~/.hermes/config.yaml`: Addition of review gate configuration settings.
