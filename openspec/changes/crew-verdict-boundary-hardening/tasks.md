# Tasks: crew-verdict-boundary-hardening

- [ ] 1. Harden `crew_card.py` verdict refusal <!-- id: task-1-crew-card-verdict -->
  - **Description**: In `oss/crew/scripts/crew_card.py::cmd_verdict`, prevent calling `record_verdict` when `not cmd` and `not is_crew_body(row[4])`.
  - **Proof**: `python -c "import sys; sys.path.insert(0, 'oss/crew/scripts'); import crew_card; assert hasattr(crew_card, 'is_crew_body')"`

- [ ] 2. Harden `crew_graph_serve.py` tile verdict gating <!-- id: task-2-tile-verdict-gating -->
  - **Description**: In `oss/crew/scripts/crew_graph_serve.py::tile_verdict`, return `None` immediately when `not CG.crew_card.is_crew_body(body)`.
  - **Proof**: `python -c "import sys; sys.path.insert(0, 'oss/crew/scripts'); import crew_graph_serve; assert callable(crew_graph_serve.tile_verdict)"`

- [ ] 3. Implement test coverage & runtime sync <!-- id: task-3-tests-and-sync -->
  - **Description**: Add `oss/crew/tests/test_crew_verdict_boundaries.py` covering refusal and tile gating. Sync changes to `_config/agent4070/hermes/plugins/crew/scripts/` and the 6 profile workspaces.
  - **Proof**: `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest oss/crew/tests/test_crew_verdict_boundaries.py -v`

- [ ] 4. Independent review & full suite regression check <!-- id: task-4-review-and-audit -->
  - **Description**: Perform independent review and execute full crew test suite ensuring failure set invariance.
  - **Proof**: `O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe -m pytest oss/crew/tests/test_crew_safety_resolution.py oss/crew/tests/test_crew_verdict_boundaries.py -v`
