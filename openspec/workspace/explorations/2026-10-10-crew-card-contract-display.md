# Exploration Report: Crew Card Contract & Specification Display

**Date**: 2026-10-10  
**Author**: Coordinator / Lead  
**Target Area**: `oss/crew/scripts/crew_graph.py`, `oss/crew/scripts/crew_dashboard/card.js`, `oss/crew/tests/test_crew_dashboard_customization.py`

## 1. Problem Statement & Motivation
Currently, on the Crew visualization dashboard (`/card/<id>`), clicking on the `card` node displays only minimal fields under the "Contract" tab:
- `Done when` (single line)
- `Writer`
- `Verifier`
- `Model pin`
- `Budget`
- `Result`

However, in the actual Hermes Kanban database (`kanban.db` `tasks.body`), the card contract contains critical technical specifications drafted by the coordinator:
- `GOAL`
- `Artifact`
- `Lands at`
- `For`
- `Inputs`
- `proof command` and `Proof mode`
- `Implementation details` (numbered technical tasks and constraints)
- Full contract text

Because `crew_graph.py::_Builder.branch()` only extracts `Done when`, `Coordinator`, and `Verifier`, users reviewing cards on the Crew Dashboard cannot see the actual contract details or implementation steps, forcing them to open Hermes CLI (`hermes kanban show`) or a separate Kanban board.

## 2. Architectural Seams & Invariants
1. **Data Layer (`oss/crew/scripts/crew_graph.py`)**:
   - `_Builder.branch()` queries `tasks` table where `body` is loaded in `row[11]`.
   - `body_field(body, key)` helper already exists in `crew_graph.py` to extract individual header fields.
   - We can extract:
     - `goal`: `body_field(body, "GOAL")` or `body_field(body, "Goal")`
     - `artifact`: `body_field(body, "Artifact")`
     - `lands_at`: `body_field(body, "Lands at")`
     - `inputs`: `body_field(body, "Inputs")`
     - `proof_cmd`: `body_field(body, "proof command")`
     - `proof_mode`: `body_field(body, "Proof mode")`
     - `body`: raw `body` text sanitized for client JSON.
   - These are included in `card_ev` and serialized into `/card/<id>.json`.

2. **Frontend Presentation (`oss/crew/scripts/crew_dashboard/card.js`)**:
   - `tabBody(n, m, tab)` handles `m.kind === "card"` and `tab === "Contract"`.
   - It will render the expanded key-value rows (`Goal`, `Artifact`, `Lands at`, `Inputs`, `Proof command`).
   - If `ev.body` exists and contains additional content (such as `Implementation details`), render a styled section `<div class="sec"><div class="label">Contract Specification & Details</div><div class="prose">...</div></div>`.

3. **Styling & Assets (`oss/crew/scripts/crew_dashboard/crew.css`)**:
   - Existing `.prose`, `.sec`, `.label`, `.mono` classes already provide clean typography and monospace styling.

4. **Testing (`oss/crew/tests/test_crew_dashboard_customization.py`)**:
   - Add unit tests verifying that `/card/<id>.json` contains `goal`, `artifact`, `lands_at`, `inputs`, `proof_cmd`, and `body`.
   - Verify that `card.js` contains the contract rendering logic for these fields.

## 3. Risk Assessment & Mitigations
- **Risk**: Large `body` payloads slowing down the graph JSON response.
  - *Mitigation*: Kanban `body` strings are typically 1-4 KB markdown text, well within lightweight HTTP payload limits (< 200 KB for complete graph).
- **Risk**: XSS from untrusted card body markdown.
  - *Mitigation*: `card.js` already runs `esc()` or `md()` escaping on all dynamic HTML strings.
