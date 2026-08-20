# M02 — Storage: спеки и КП

## Run directory layout

```text
runs/{run_id}/
├── status.json              # state machine source of truth
├── input/
│   └── spec-client-alpha.xlsx    # copy from inbox, read-only
├── rows.json
├── lineitems.json
├── offers.json
├── selection.json
├── needs-review.json
├── sources.log              # audit: catalog/s4b/web queries
└── .phase-lock              # optional distributed lock file
```

## Phase artifacts matrix

| Phase | Writes | Reads |
| --- | --- | --- |
| ingest | input/, rows.json | inbox |
| classify | lineitems.json, needs-review.json | rows.json |
| search | offers.json, sources.log | lineitems.json, M04 catalogs |
| rank | selection.json | offers.json, lineitems.json |
| variants | commerce.sqlite | selection.json, offers.json |
| review | status flags | all |
| final | status.json phase=final | all |

## sources.log format

```text
2026-08-20T07:20:01Z catalog db=distrib-main pn=910-001793 hits=3
2026-08-20T07:20:05Z s4b pn=910-001793 in_stock=2 skipped_on_order=1
2026-08-20T07:21:00Z web shop=dns query="Logitech M185" hits=5
```

S4B lines **only** if cabinet profile electronics-procurement.

## Extracted markdown

For xlsx/xls/csv upload to inbox:

```text
inbox/spec.xlsx.extracted.md
```

Parser reads extracted.md in agent context, not binary.

## export/

```text
export/kp-{run_id}-{timestamp}.xlsx
export/kp-{run_id}-{timestamp}.meta.json
```

meta.json:

```json
{
  "run_id": "01JABC1234567890",
  "template_version": "kp-template-v3",
  "generated_at": "2026-08-20T07:30:00Z",
  "operator_finalized": true
}
```

## Locking

- `.phase-lock`: `{ "holder": "job_id", "expires": "..." }`
- Stale lock > 30min → steal with audit

## Negative test IDs

| Test ID | Сценарий |
| --- | --- |
| NEG-SKP-ST-001 | Delete offers.json mid-rank |
| NEG-SKP-ST-002 | Modify input/ after classify |
| NEG-SKP-ST-003 | sources.log s4b entry on generic cabinet |
