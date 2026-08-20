# M02 — Security: спеки и КП

## Anti-hallucination

| Rule | Enforcement |
| --- | --- |
| Prices only from offers.json/MCP | Phase guard + export validation |
| No agent-written kp.xlsx | Export API only; sandbox write deny export/ by agent |
| No P/N mutation | Diff check classify output vs raw_text |
| final requires operator | RBAC + audit |

## S4B policy

- Tool registration gated by M00 capabilities
- `in_stock_only: true` hardcoded in commerce-s4b
- on_order results stripped at search layer
- Non-electronics: NEG-SKP-007 must pass

## Upload security

- MIME validation + magic bytes
- Max size 25 MiB
- No macros execution — parse as data only
- Virus scan hook (optional ClamAV)

## commerce.sqlite

- Parameterized queries only in MCP
- No arbitrary SQL from agent in production (allowlist statements)

## Audit

Log: run created, phase advance, finalize, export/kp, re-search sources.

## PII

Spec files may contain addresses — export metadata redacts in logs.

## Negative test IDs

| Test ID | Focus |
| --- | --- |
| NEG-SKP-SEC-001 | Inject fake offer via API |
| NEG-SKP-SEC-002 | Export before finalize |
| NEG-SKP-SEC-003 | XSS in raw_text display (UI escape) |
