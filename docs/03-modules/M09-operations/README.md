# M09 — Operations (audit, metrics, retention)

Модуль **операционной observability**: схема `audit_events`, учёт **token usage**, **MCP metrics** (имена Prometheus), политики **retention**.

## Scope

| компонент | описание |
| --- | --- |
| Audit log | Append-only события всех модулей |
| Token accounting | LLM usage per tenant/cabinet/run |
| MCP metrics | Prometheus exporters |
| Retention jobs | TTL по типам данных |
| Dashboards | Grafana templates |

## Consumers

- Tenant admin — audit UI, usage billing
- Platform ops — Prometheus/Grafana
- Compliance — export audit trail

## Документация

- [domain.md](domain.md) — AuditEvent, TokenUsageRecord
- [api.md](api.md) — query audit, usage reports
- [persistence.md](persistence.md) — schemas, partitions
- [storage.md](storage.md) — cold archive
- [mcp-tools.md](mcp-tools.md) — query tools
- [ui.md](ui.md) — audit viewer
- [security.md](security.md) — PII in audit
- checklists

## Принципы

1. Audit **append-only** — no UPDATE/DELETE except retention job.
2. Tenant isolation — same RLS as M08.
3. Metrics labels include `tenant_id`, `cabinet_id` where cardinality allows.
4. Retention defaults documented, overridable per plan.
