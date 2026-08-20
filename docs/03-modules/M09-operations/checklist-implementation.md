# M09 — Чеклист реализации

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 1.1 | audit_events schema + partitions | | Monthly auto-create |
| 1.2 | internal/audit/emit API | | All modules wired |
| 1.3 | event_type taxonomy documented | | Prefix registry |
| 1.4 | payload redaction | | Secret grep CI |
| 1.5 | RLS tenant isolation | | Cross-tenant fail |
| 2.1 | token_usage on run.completed | | M07 hook |
| 2.2 | cost estimation formula | | Per model table |
| 2.3 | token_usage_daily mat view | | Hourly refresh |
| 2.4 | Usage API group_by | | day/cabinet/model |
| 2.5 | Billing export CSV | | |
| 3.1 | prodavan_mcp_tool_calls_total | | All labels |
| 3.2 | prodavan_mcp_discovery_duration_seconds | | Histogram |
| 3.3 | prodavan_integration_* metrics | | M05 wired |
| 3.4 | prodavan_agent_* metrics | | M07 wired |
| 3.5 | prodavan_llm_tokens_total | | Counter |
| 4.1 | /metrics endpoint | | Prometheus scrape |
| 4.2 | Grafana dashboards (4) | | JSON in repo |
| 4.3 | Alert rules starter | | |
| 4.4 | Cardinality controls | | No unbounded labels |
| 4.5 | metric_hints tool/doc | | |
| 5.1 | Retention job audit_events | | Archive then drop |
| 5.2 | Retention stream_events | | 90d |
| 5.3 | Retention integration_call_log | | 90d |
| 5.4 | retention_jobs logging | | |
| 5.5 | Dry-run mode | | |
| 6.1 | Audit UI filters + table | | |
| 6.2 | Export async job | | |
| 6.3 | Usage dashboard | | |
| 6.4 | Platform retention admin | | |
| 6.5 | Runbooks linked | | |

**Порог:** средний ≥ 8, all modules emit audit, metrics names match doc.
