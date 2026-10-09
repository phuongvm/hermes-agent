# Exploration Report: Crew Verdict Boundary Hardening for Ad-hoc & Non-Contract Cards

**Date**: 2026-10-09  
**Topic**: Hardening Crew verdict recording and dashboard tile classification boundaries to prevent non-crew cards from entering `unverified` states  
**Target Codebases**: `oss/crew`  
**Reference Incident**: Kanban task `t_9fd93b34` on `skills-kb` board  

---

## 1. Architectural Baseline & Root Cause

### 1.1 Crew Verdict Recording Protocol
In `oss/crew/scripts/crew_card.py`:
- `cmd_verdict(args)` executes the owner-confirmed proof command for a card via `close_proof_command(args.card)`.
- If `cmd` is empty (the card lacks an owner snapshot event `origin` or `proof_confirm`), `cmd_verdict` prints a warning and immediately invokes:
  ```python
  rec = record_verdict(args.card, "", 1, "no owner-confirmed proof command on the card", 0, by=args.by)
  ```
- This writes a persistent JSON line with `verdict: "FAIL"` to `$HERMES_HOME/crew/verdicts/<card_id>.jsonl`.
- **Architectural Seam Flaw**: `cmd_verdict` does not verify if the card is actually an authentic Crew contract (`is_crew_body(row[4])`). When a worker runs `crew_card.py verdict` on an ad-hoc or generic Kanban card (e.g. child card created via `kanban_create` during technical debt remediation), it permanently poisons the verdict log with a FAIL line.

### 1.2 Dashboard Tile Verdict Evaluation
In `oss/crew/scripts/crew_graph_serve.py`:
- `tile_verdict(db, card_id, body)` evaluates:
  ```python
  verdicts = CG.crew_card.all_verdicts(card_id)
  if not verdicts and not CG.body_field(body, "Verifier"):
      return None
  claims = [ts for kind, ts, _ in CG.load_task_events(db, card_id) if kind == "claimed"]
  since = CG.crew_card.verdict_lines(card_id, max(claims) if claims else None, verdicts)
  last = since[-1] if since else None
  verdict = str((last or {}).get("verdict") or "").upper()
  return verdict if verdict in ("PASS", "FAIL") else "unverified"
  ```
- **Architectural Seam Flaw**: If any verdict line exists in `all_verdicts(card_id)` (such as the orphaned FAIL from the coder), `verdicts` is not empty. When a subsequent reviewer verifies the card outside of `crew_card.py verdict` (e.g., using `openspec-verifier` in terminal) and closes the card via `kanban_complete`, `since` (verdict lines after reviewer's claim) contains zero items.
- As a result, `tile_verdict` falls through to `"unverified"`, causing the Crew UI to display an amber `○ unverified` chip for a completely passed and closed task.

---

## 2. Surgical Hardening Strategy

### 2.1 Refusal Without Log Poisoning in `crew_card.py`
In `cmd_verdict(args)`:
- Check `is_crew_body(row[4])`. If `not cmd`:
  - If `is_crew_body(row[4])`: record the FAIL line as before (preserving strict Crew contract enforcement).
  - If `not is_crew_body(row[4])`: print a refusal message explaining that the card is not a Crew contract card and exit with code 1, **without** calling `record_verdict()`.

### 2.2 Strict Crew Contract Gating in `crew_graph_serve.py`
In `tile_verdict(db, card_id, body)`:
- Add a fast pre-check:
  ```python
  if not CG.crew_card.is_crew_body(body):
      return None
  ```
- If a card body does not carry a `Role:` or `Coordinator:` header, it is an external / generic Kanban card. The dashboard should never assign it a Crew verdict chip, eliminating false-positive `unverified` states regardless of historical filesystem artifacts.

### 2.3 Comprehensive Verification
- Add automated pytest cases in `oss/crew/tests/test_crew_verdict_boundaries.py`.
- Sync changes across all 8 runtime script copies (`_config/agent4070/hermes/plugins/crew/scripts/` and the 6 profile plugins).
- Re-run full test suite to guarantee zero regression on baseline tests.
