# Architecture & Design: Crew Card Contract Display

## 1. Technical Architecture
The enhancement operates across the data serialization pipeline and the frontend view renderer:

```text
[ SQLite kanban.db (tasks.body) ]
                 │
                 ▼
[ crew_graph.py: _Builder.branch() ]
  Extracts:
  - goal: body_field(body, "GOAL") || body_field(body, "Goal")
  - artifact: body_field(body, "Artifact")
  - lands_at: body_field(body, "Lands at")
  - inputs: body_field(body, "Inputs")
  - proof_cmd: body_field(body, "proof command")
  - proof_mode: body_field(body, "Proof mode")
  - body: raw body text
                 │
                 ▼
[ /card/<id>.json ] ───> [ card.js: tabBody(n, m, "Contract") ]
                           Renders:
                           - Key-value definition list (<dl>)
                           - Markdown-rendered specification block (<div class="sec">)
```

## 2. Component Design

### Backend (`crew_graph.py`)
In `_Builder.branch()`:
```python
card_ev["goal"] = body_field(body, "GOAL") or body_field(body, "Goal")
card_ev["artifact"] = body_field(body, "Artifact")
card_ev["lands_at"] = body_field(body, "Lands at")
card_ev["inputs"] = body_field(body, "Inputs")
card_ev["proof_cmd"] = body_field(body, "proof command")
card_ev["proof_mode"] = body_field(body, "Proof mode")
card_ev["body"] = body or ""
```

### Frontend (`card.js`)
In `tabBody(n, m, tab)` for `m.kind === "card"` and `tab === "Contract"`:
- Render key-value rows for `Goal`, `Artifact`, `Lands at`, `Inputs`, `Proof command`.
- Render a dedicated contract section with `md(ev.body)` if additional content (such as `Implementation details`) is present.
