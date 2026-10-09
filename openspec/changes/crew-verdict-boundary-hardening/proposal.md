# Change Proposal: crew-verdict-boundary-hardening

## 1. Problem Statement
When ad-hoc or generic Kanban tasks (such as technical debt cards created via `kanban_create` without a Crew contract) are worked on, workers may reflexively invoke `crew_card.py verdict --card <id>`. Because the card lacks an owner proof snapshot, `cmd_verdict` currently writes a persistent `FAIL` record to disk. Subsequently, when the task is independently verified via test suites and approved with `kanban_complete`, the Crew Dashboard (`crew_graph_serve.py`) evaluates the presence of this orphaned verdict file and flags the task as `unverified` (amber chip), confusing users and breaking verification visibility.

## 2. Proposed Changes
1. **Refusal without Poisoning in `oss/crew/scripts/crew_card.py`**:
   - In `cmd_verdict`, check `is_crew_body(row[4])`. If `not cmd` and `not is_crew_body(row[4])`, print a refusal message and exit 1 without writing a `FAIL` verdict log to disk.
2. **Dashboard Gating in `oss/crew/scripts/crew_graph_serve.py`**:
   - In `tile_verdict`, verify `if not CG.crew_card.is_crew_body(body): return None`. Non-crew cards will never be assigned a Crew verdict chip, eliminating false-positive `unverified` states regardless of historical filesystem artifacts.
3. **Automated Regression Prevention**:
   - Add unit test coverage in `oss/crew/tests/test_crew_verdict_boundaries.py`.
   - Synchronize identical script copies across `_config/agent4070/hermes/plugins/crew/scripts/` and the 6 profile plugin workspaces.

## 3. Impact Assessment
- **Zero Regression**: Existing Crew contracts (`is_crew_body(body) == True`) retain 100% of their existing strict Zero-Trust enforcement and owner proof confirmation checks.
- **Clean Dashboard**: Ad-hoc and follow-up Kanban tasks render cleanly as standard completed cards without false `unverified` warning chips.
