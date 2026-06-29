## Architecture

The Review Gate introduces an asynchronous pause in the teams_pipeline webhook processor.

1. **Extraction and Generation**: The webhook triggers the pipeline as usual, extracting transcript data and generating the summary.
2. **Interception**: Instead of proceeding directly to _write_sinks() to deliver the summary, the pipeline checks a require_review configuration flag.
3. **State Management**: If require_review is true, the job state is updated to pending_review in the local database (or relevant state store) instead of completed.
4. **Notification**: The pipeline sends a notification with a draft of the summary to the Commander via the Matrix channel.
5. **Approval and Delivery**: A new CLI command (e.g., hermes teams-pipeline deliver job_id) is exposed. The Commander approves the summary in Matrix, triggering this command to finalize the _write_sinks() execution and mark the job as completed.

## Components

- **plugins/teams_pipeline/pipeline.py**:
  - Intercept the pipeline flow before the delivery sinks are invoked.
  - Check configuration for require_review.
  - Update job status to pending_review.
  - Trigger Matrix notification with the generated summary content.
- **plugins/teams_pipeline/cli.py**:
  - Add a deliver (or approve) command that takes a job ID.
  - This command retrieves the pending job, executes the sinks (_write_sinks), and updates the status to completed.
- **plugins/platforms/teams/adapter.py**:
  - Ensure TeamsSummaryWriter can be called reliably when disconnected from the immediate webhook trigger context.
- **Configuration (config.yaml)**:
  - Add settings to enable/disable the review gate per pipeline or globally.
  - Map notification channels (e.g., Matrix room ID).

## Data Flow

1. Webhook Payload -> run_notification
2. Extract Data -> Generate Summary
3. Check config.teams_pipeline.require_review
   - If False -> _write_sinks() -> End
   - If True -> Set state pending_review -> Send Matrix Notification -> End
4. Commander explicitly approves -> triggers hermes teams-pipeline deliver job_id
5. Retrieve job -> _write_sinks() -> Update state completed -> End

## Alternatives Considered

- **Ad-hoc Python wrapper script**: Rejected based on Hermes Engineering Principles; system changes must be structural and reliable, not patched via external scripts wrapping native commands without state management.
- **Master Orchestrator Skill for Online Webhooks**: While used for offline pulls, webhooks are event-driven pushes processed automatically by internal daemons. Intercepting the daemon pipeline natively is cleaner than trying to catch webhook events externally with an orchestrator skill.
