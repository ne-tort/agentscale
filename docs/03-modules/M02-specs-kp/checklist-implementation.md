# M02 — Checklist: implementation

## Upload & ingest

- [ ] POST inbox/upload multipart
- [ ] Extracted.md for xlsx/xls/csv
- [ ] POST /runs creates input/ copy
- [ ] rows.json writer (honest not_implemented if parser missing)

## State machine

- [ ] status.json phase_history
- [ ] Phase guards INV-SKP-002
- [ ] POST advance async jobs
- [ ] SSE events endpoint
- [ ] .phase-lock

## classify / search / rank

- [ ] lineitems.json schema
- [ ] Search cascade catalog → s4b* → web
- [ ] S4B in_stock only, skip on_order
- [ ] selection.json primary rules
- [ ] sources.log

## variants & SQLite

- [ ] commerce.sqlite schema migration
- [ ] import-run idempotent
- [ ] MCP commerce-offers batch upsert

## Review & export

- [ ] POST finalize RBAC
- [ ] POST export/kp from template
- [ ] meta.json sidecar

## Equipment (electronics)

- [ ] GET/POST equipment API
- [ ] MCP commerce-equipment
- [ ] Hidden on non-electronics

## Tests

- [ ] NEG-SKP-001 … 007
- [ ] Full pipeline integration test electronics
- [ ] Generic cabinet: no s4b in sources.log
