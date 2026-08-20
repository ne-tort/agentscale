# M02 — UI: спеки и КП

## Inbox panel

- Drag-drop zone: xlsx, xls, csv, txt
- File list with upload date, link to extracted.md preview
- Action «Новый прогон» per file

## Run timeline

**Route:** `/c/{cid}/p/{pid}/runs/{run_id}`

Visual pipeline:

```text
[ingest] → [classify] → [search] → [rank] → [variants] → [review] → [final]
   ✓          ✓           ● running
```

- Click completed phase → view artifact JSON (read-only tree)
- needs_review badge count on classify
- S4B chip on search phase **only** electronics cabinet

## Line items table

Columns: raw_text, category, P/N, qty, confidence, needs_review, primary offer preview.

Row actions: re-search line, open variants drawer.

## Variants drawer

- Primary + alternatives tabs
- Source badge: catalog | s4b | web
- Trusted seller icon
- Price, stock, as_of

## Review & finalize

- Checkbox per line or bulk «принять все primary»
- Block finalize if needs_review > threshold (configurable)
- Button «Экспорт КП» → POST export/kp → download

## Equipment cards (electronics)

**Route:** `/c/{cid}/p/{pid}/equipment`

- Grid of cards by category
- Detail: specs, analogs, compatibility
- Hidden entirely if !capabilities.equipment_cards

## Empty / error states

| State | UI |
| --- | --- |
| Parse not implemented | Honest banner, empty rows — not fake data |
| Search empty | «Нет офферов» + suggest web re-search |
| S4B rate limited | Banner from M04 cred state |

## Negative test IDs (UI)

| Test ID | Сценарий |
| --- | --- |
| NEG-SKP-UI-001 | S4B badge hidden on generic |
| NEG-SKP-UI-002 | Finalize without operator role |
| NEG-SKP-UI-003 | Equipment nav hidden non-electronics |
