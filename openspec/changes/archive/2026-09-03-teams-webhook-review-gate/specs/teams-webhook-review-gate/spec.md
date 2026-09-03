## ADDED Requirements

### Requirement: Human-in-the-Loop Review Gate
The webhook pipeline MUST check configuration for a `require_review` flag before executing sink writers. When true, delivery is paused.

#### Scenario: Review flag is enabled
- **WHEN** `teams_pipeline.require_review` is true in configuration
- **THEN** pipeline execution pauses before `_write_sinks()` and job is marked `pending_review`

### Requirement: Pending Notification
The pipeline MUST notify the designated Commander channel that a summary is awaiting review when a job is paused.

#### Scenario: Job enters pending state
- **WHEN** job state transitions to `pending_review`
- **THEN** a Matrix notification containing the summary draft is sent to the Commander

### Requirement: Manual Delivery CLI Command
A CLI command `hermes teams-pipeline deliver <job_id>` MUST be provided to manually trigger the `_write_sinks` phase for a pending job.

#### Scenario: Commander approves pending job
- **WHEN** the `deliver` CLI command is executed for a valid `pending_review` job ID
- **THEN** the pipeline completes `_write_sinks` and job state transitions to `completed`
