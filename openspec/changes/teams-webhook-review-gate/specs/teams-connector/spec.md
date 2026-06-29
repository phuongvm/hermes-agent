## ADDED Requirements

### Requirement: Automated Sink Execution
The Teams meeting pipeline MUST automatically execute sink writers at the end of the extraction process, UNLESS the review gate configuration specifies otherwise.

#### Scenario: Review flag is disabled or unset
- **WHEN** `teams_pipeline.require_review` is false or missing
- **THEN** pipeline execution proceeds automatically to `_write_sinks()`

#### Scenario: Review flag is enabled
- **WHEN** `teams_pipeline.require_review` is true
- **THEN** pipeline execution DOES NOT automatically call `_write_sinks()` and instead stores the intermediate state
