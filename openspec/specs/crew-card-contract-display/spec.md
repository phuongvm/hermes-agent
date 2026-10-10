# crew-card-contract-display Specification

## Purpose
Defines requirements and verification scenarios for displaying comprehensive card contract metadata, artifacts, landing locations, inputs, proof commands, and full specification details under the Contract tab on the Crew coordination dashboard.
## Requirements
### Requirement: Comprehensive Card Contract Metadata Display
The Crew card view MUST display full contract specification fields under the "Contract" tab when viewing a coordinator card node.

#### Scenario: Card has full contract body
- GIVEN a task exists in the active Kanban database with `GOAL`, `Artifact`, `Lands at`, `Inputs`, `proof command`, and `Implementation details` in its body
- WHEN `/card/<id>.json` is fetched
- THEN `card_ev` MUST contain `goal`, `artifact`, `lands_at`, `inputs`, `proof_cmd`, and `body`
- AND the rendered HTML under the "Contract" tab MUST display the goal, target artifact, landing path, proof command, and full specification text.

#### Scenario: Card has minimal or legacy body
- GIVEN a legacy task has only a single-line body or lacks structured fields
- WHEN `/card/<id>.json` is fetched
- THEN `card_ev` MUST gracefully omit empty fields without throwing errors or breaking the UI layout.

