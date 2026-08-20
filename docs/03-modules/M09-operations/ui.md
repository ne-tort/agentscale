# M09 — UI

## Audit log viewer

Route: `/cabinet/{id}/audit` or `/tenant/audit`

### Filters panel

- Date range picker
- Event type multiselect (grouped by prefix)
- Actor (user dropdown)
- Cabinet (tenant view only)
- Free text search in payload (indexed fields only)

### Event table

| Time | Actor | Event | Resource | Details |
| --- | --- | --- | --- | --- |
| 10:05 | ivan@... | agent.run_cancelled | run uuid | expand JSON |

Expand row → formatted payload, redacted fields shown as `[REDACTED]`.

### Export button

→ async export modal, progress, download link.

## Token usage dashboard

Route: `/tenant/usage`

### Widgets

- Total tokens this month (input/output)
- Estimated cost USD
- Chart: daily usage line
- Breakdown table: by cabinet, by model

### Drill-down

Click cabinet → cabinet usage → project list → run list (links to M07).

## Platform ops (Grafana embed)

Route: `/platform/metrics` — iframe or link to Grafana `prodavan-mcp-overview`.

## Retention status (platform.admin)

Table:

| data_type | retention_days | last_job | rows_deleted |
| --- | --- | --- | --- |
| audit_events | 365 | 2026-08-19 | 1200000 |

Button «Run retention dry-run».

## Alerts inbox (later)

Tenant-visible: rate limit warnings, MCP discovery errors for their cabinet.

## UX rules

- Audit default sort: newest first
- Event type human-readable labels (RU)
- Never show payload secrets — pre-redacted server-side
- Large payload → link «Скачать полный payload» (role gated)

## Empty states

- No events: «За период событий нет»
- Usage zero: onboarding hint «Начните чат в проекте»
