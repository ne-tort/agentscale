# M07 — Чеклист реализации

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 1.1 | Project CRUD + filesystem init | | AGENTS.md copied |
| 1.2 | Session create/resume | | Per user per project |
| 1.3 | Message POST + run queue | | 202 + run_id |
| 1.4 | reset lifecycle | | MCP re-bind |
| 1.5 | cancel lifecycle | | Provider abort |
| 2.1 | SSE stream stable events | | Contract tests |
| 2.2 | Last-Event-ID replay | | Reconnect E2E |
| 2.3 | WebSocket bidirectional | | Parity with SSE |
| 2.4 | Extended events feature flag | | Off by default |
| 2.5 | eventParserVersion | | Documented |
| 3.1 | Message persistence + sequence | | Ordered |
| 3.2 | stream_events partition | | Retention job |
| 3.3 | Large payload blob offload | | >32KB |
| 3.4 | Token usage on complete | | M09 insert |
| 3.5 | Archived sessions on reset | | Queryable |
| 4.1 | Attachment upload → inbox | | Path correct |
| 4.2 | xlsx extracted.md async | | Commerce parity |
| 4.3 | MIME + size validation | | 413 tests |
| 4.4 | ClamAV hook | | EICAR test |
| 4.5 | Inbox quota | | 507 handling |
| 5.1 | Model picker + allowlist | | 403 unknown |
| 5.2 | M06 session-bind integration | | Tools work E2E |
| 5.3 | Redaction middleware | | No secrets in stream |
| 5.4 | Chat UI streaming | | Playwright |
| 5.5 | Debug drawer | | Redacted tool output |
| 6.1 | Rate limits messages/SSE | | 429 |
| 6.2 | RLS cross-project | | Fail test |
| 6.3 | Audit events | | M09 |
| 6.4 | Runbook stream failure | | |

**Порог релиза:** средний ≥ 8, SSE replay 10/10.
