## Phase 1: Specifications

- [x] Write capability spec specs/teams-webhook-review-gate/spec.md.
- [x] Write delta spec specs/teams-connector/spec.md (if applicable, or skip if only adding capability).

## Phase 2: Configuration and State Management

- [x] Update config schema to support teams_pipeline.require_review flag.
- [x] Add pending_review status to job state tracking (database/local state store).

## Phase 3: Pipeline Interception

- [x] Modify plugins/teams_pipeline/pipeline.py to intercept execution before _write_sinks().
- [x] Implement Matrix notification logic for pending jobs within the pipeline processor.

## Phase 4: CLI Delivery Command

- [x] Add deliver command to plugins/teams_pipeline/cli.py to process pending jobs by ID.
- [x] Ensure TeamsSummaryWriter properly executes state update to completed upon delivery.

## Phase 5: Testing

- [x] Write/run tests for online webhook flow with require_review=true (ensure pause state and notification).
- [x] Write/run tests for hermes teams-pipeline deliver command.
