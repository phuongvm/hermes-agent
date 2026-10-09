# Design: crew-verdict-boundary-hardening

## 1. Overview
This design isolates Crew's verdict logging and dashboard rendering mechanisms so that ad-hoc Kanban tasks created outside the `/crew` intake lifecycle do not accidentally acquire false negative verdicts or get labeled as `unverified`.

## 2. Technical Design

### 2.1 `oss/crew/scripts/crew_card.py`
In function `cmd_verdict(args)`:
```python
if not cmd:
    if not is_crew_body(row[4]):
        print("card %s is not a crew contract card; verdict recording refused" % args.card)
        return 1
    print("card %s has no owner-confirmed proof command; FAIL until the owner confirms one" % args.card)
    rec = record_verdict(args.card, "", 1, "no owner-confirmed proof command on the card", 0, by=args.by,
                         for_event=for_event)
    rc = 1
```

### 2.2 `oss/crew/scripts/crew_graph_serve.py`
In function `tile_verdict(db, card_id, body)`:
```python
def tile_verdict(db, card_id, body):
    if not CG.crew_card.is_crew_body(body):
        return None
    verdicts = CG.crew_card.all_verdicts(card_id)
    if not verdicts and not CG.body_field(body, "Verifier"):
        return None
    claims = [ts for kind, ts, _ in CG.load_task_events(db, card_id) if kind == "claimed"]
    since = CG.crew_card.verdict_lines(card_id, max(claims) if claims else None, verdicts)
    last = since[-1] if since else None
    verdict = str((last or {}).get("verdict") or "").upper()
    return verdict if verdict in ("PASS", "FAIL") else "unverified"
```

## 3. Test & Verification Plan
- Author `oss/crew/tests/test_crew_verdict_boundaries.py` testing:
  1. `cmd_verdict` with mock non-crew card returns rc=1 and does not create verdict files.
  2. `cmd_verdict` with mock crew card without proof snapshot preserves existing behaviour (records FAIL).
  3. `tile_verdict` returns `None` for non-crew cards even when `all_verdicts` returns records.
- Sync across all 8 runtime script copies and verify SHA256 equality.
