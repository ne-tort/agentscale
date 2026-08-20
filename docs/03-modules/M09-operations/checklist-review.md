# M09 — Чеклист ревью

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| R1 | audit_events append-only? | | UPDATE allowed |
| R2 | All M05–M08 events emitted? | | Silent modules |
| R3 | Metric names match mcp-tools.md? | | Renamed without doc |
| R4 | Retention before DROP? | | Data loss no archive |
| R5 | Token usage on every run? | | Skipped on error |
| S1 | Secrets in audit payload? | | password in JSON |
| S2 | Audit query tenant isolated? | | Cross-tenant leak |
| S3 | /metrics not public? | | Open internet |
| S4 | Export signed URL TTL? | | Permanent link |
| S5 | Retention DELETE role separate? | | App user DELETE |
| A1 | Cursor pagination works? | | Offset on huge table |
| A2 | AUDIT_QUERY_TOO_WIDE enforced? | | Full table scan |
| A3 | Export async 202? | | Sync timeout |
| A4 | Usage API max period? | | Unbounded query |
| A5 | payload_ref for large events? | | 10MB rows |
| T1 | Partition detach tested? | | |
| T2 | Mat view refresh concurrent? | | Lock |
| T3 | PromQL alerts valid? | | Syntax error |
| T4 | Emit failure retry? | | Lost events |
| T5 | Redaction unit tests? | | |
| O1 | Grafana dashboards load? | | |
| O2 | Alert McpDiscoveryFailureSpike fires? | | |
| O3 | retention_jobs auditable? | | |
| O4 | Cold archive checksum verified? | | |
| O5 | GDPR export complete? | | Missing audit |

**Verdict:** Reject if S1, S2, R4 red flags.

**Ревьюер:** _______________  
**Дата:** _______________  
**Средний балл:** ___ / 10
