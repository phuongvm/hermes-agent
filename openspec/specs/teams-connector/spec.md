# Spec: teams-connector

## Purpose

Microsoft Teams integration connector for Hermes Agent — handles meeting lifecycle events via Microsoft Graph webhooks, transcription retrieval, and automated meeting summary delivery through configurable sinks (Notion, Linear, Teams).

## Requirements

### Requirement: Webhook-driven meeting pipeline
The connector SHALL process Microsoft Graph webhook notifications for online meeting events.

#### Scenario: Meeting event processing
- **WHEN** a Graph webhook notification is received for a meeting lifecycle event
- **THEN** the pipeline resolves the meeting, retrieves transcripts and artifacts, generates a summary, and delivers to configured sinks

### Requirement: Review gate interception
The connector SHALL support a `require_review` configuration flag that intercepts automated delivery.

#### Scenario: Review gate enabled
- **WHEN** `teams_pipeline.require_review` is enabled
- **THEN** the pipeline stores the generated summary, sets job state to `pending_review`, and notifies the Commander channel instead of delivering automatically

#### Scenario: Review gate disabled
- **WHEN** `teams_pipeline.require_review` is false or not set
- **THEN** the pipeline proceeds automatically to sink delivery (legacy behavior)
