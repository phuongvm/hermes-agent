# Implementation Tasks: Crew Card Contract & Specification Display

- [x] Task 1: Extract contract metadata and body in `crew_graph.py`
  - In `_Builder.branch()`, parse `goal`, `artifact`, `lands_at`, `inputs`, `proof_cmd`, `proof_mode`, and include full `body` in `card_ev`.
  - Proof: `python -c "import sys; sys.path.insert(0, 'oss/crew/scripts'); import crew_graph as cg; g, _ = cg.build_graph('t_31c46d1b'); ev = next(n['evidence'] for n in g['nodes'] if n['kind'] == 'card'); assert 'goal' in ev and 'body' in ev; print('Backend contract serialization verified')"`

- [x] Task 2: Render comprehensive contract view in `card.js`
  - In `tabBody(n, m, "Contract")`, render `Goal`, `Artifact`, `Lands at`, `Inputs`, `Proof command`, and the full contract specification text in `card.js`.
  - Proof: `python -c "js = open('oss/crew/scripts/crew_dashboard/card.js').read(); assert 'ev.goal' in js and 'ev.body' in js; print('Frontend contract rendering verified')"`

- [x] Task 3: Add unit tests and synchronize runtime files
  - Add test cases in `oss/crew/tests/test_crew_dashboard_customization.py` verifying contract extraction and rendering.
  - Synchronize updated files to `_config/agent4070/hermes/plugins/crew/`.
  - Proof: `pytest oss/crew/tests/test_crew_dashboard_customization.py`
