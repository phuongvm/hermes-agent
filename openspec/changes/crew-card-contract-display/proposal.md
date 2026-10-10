# Change Proposal: Crew Card Contract & Specification Display

## 1. Summary
Enhance the Crew detail view (`/card/<id>`) on both Hermes Desktop and Web Dashboard to display the full technical contract specification (Goal, Artifact, Lands at, Inputs, Proof command, and full Implementation details) under the "Contract" tab of the coordinator card node, eliminating the visibility gap between Kanban and Crew.

## 2. Motivation
Currently, users inspecting a task on the Crew Dashboard can only see brief metadata and a single-line `Done when` clause. The rich technical contract—including step-by-step implementation details, target artifacts, inputs, and proof commands—is hidden on the dashboard, requiring users to switch to Hermes CLI or native Kanban to review the contract.

## 3. Scope
- `oss/crew/scripts/crew_graph.py`: Parse and serialize `goal`, `artifact`, `lands_at`, `inputs`, `proof_cmd`, `proof_mode`, and full `body` into `card_ev`.
- `oss/crew/scripts/crew_dashboard/card.js`: Render expanded contract fields and full specification details under the "Contract" tab.
- `oss/crew/tests/test_crew_dashboard_customization.py`: Unit tests asserting serialization and rendering of contract details.
- Runtime synchronization across `_config/agent4070/hermes/plugins/crew/` and desktop plugins.

## 4. Success Criteria
- Navigating to `/card/<id>` on any task renders `Goal`, `Artifact`, `Lands at`, `Inputs`, `Proof command`, and `Contract Specification & Details` containing the complete implementation details.
- All unit tests pass cleanly with 0 regressions.
