# Capability: crew-verdict-boundary-hardening

## Purpose
Hardens Crew verdict logging and dashboard rendering boundaries so that ad-hoc, parent-spawned, or non-contract Kanban tasks do not acquire false negative verdicts or enter `unverified` states on the Crew dashboard.

## Requirements

### Requirement: REQ-CREW-VERDICT-1: Non-Crew Card Verdict Refusal
`crew_card.py verdict --card <card_id>` SHALL NOT write a verdict record (`FAIL` or `PASS`) to disk if the target card has no owner-confirmed proof command AND is not an authentic Crew contract (`is_crew_body(body)` is false). It SHALL exit with returncode 1 and output an explanatory refusal message.

#### Scenario: Running verdict on a generic Kanban card
- **GIVEN** a task created without Crew `Role:` or `Coordinator:` body headers
- **WHEN** `crew_card.py verdict --card <id>` is executed
- **THEN** the script outputs a refusal message, exits with code 1, and no file is created under `$HERMES_HOME/crew/verdicts/<id>.jsonl`.

### Requirement: REQ-CREW-DASHBOARD-1: Non-Crew Card Tile Verdict Neutrality
`crew_graph_serve.py::tile_verdict` SHALL return `None` for any card whose body is not an authentic Crew contract (`is_crew_body(body)` is false), regardless of whether historical verdict files or claims exist for that card ID.

#### Scenario: Rendering tile for a generic Kanban card with legacy verdict files
- **GIVEN** a generic Kanban card with a legacy `.jsonl` file in a profile verdicts directory
- **WHEN** `tile_verdict(db, card_id, body)` is evaluated
- **THEN** it returns `None`, and the dashboard displays no verdict chip.
