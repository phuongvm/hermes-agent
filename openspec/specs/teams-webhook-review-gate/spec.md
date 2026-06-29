# Spec: teams-webhook-review-gate

## Purpose

Human-in-the-Loop review mechanism for automated online meeting summary generation via webhooks, pausing delivery until explicit approval is granted.

## Requirements

### Requirement: Configuration-driven review gate
The webhook pipeline SHALL check configuration for a `require_review` flag before executing sink writers.

#### Scenario: Review gate activation
- **WHEN** `require_review` is true
- **THEN** the pipeline stores the generated summary and sets the job state to `pending_review`

### Requirement: Commander notification
The pipeline SHALL notify the designated Commander channel (Matrix) when a summary is awaiting review.

#### Scenario: Pending review notification
- **WHEN** a job transitions to `pending_review` state
- **THEN** a notification is sent to the Commander channel with the job ID and summary title

### Requirement: CLI-driven delivery
A CLI command `hermes teams-pipeline deliver <job_id>` SHALL be provided to manually trigger the `_write_sinks` phase for a pending job.

#### Scenario: Manual delivery
- **WHEN** the Commander runs `hermes teams-pipeline deliver <job_id>` on a `pending_review` job
- **THEN** the pipeline executes sink delivery and transitions the job state to `completed`

#### Scenario: Non-pending job rejection
- **WHEN** the Commander runs `hermes teams-pipeline deliver <job_id>` on a job not in `pending_review` state
- **THEN** the command rejects with an error message indicating the current job state

### Requirement: Legacy behavior preservation
If `require_review` is false or not set, the pipeline SHALL proceed automatically to sinks.

#### Scenario: Automatic delivery
- **WHEN** `require_review` is false or absent
- **THEN** the pipeline delivers to sinks immediately after summary generation (legacy behavior)
