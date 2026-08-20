# M09 — HTTP API

## Audit

### GET `/api/v1/tenants/{tenant_id}/audit/events`

**Query:**

```text
?cabinet_id=
&event_type=agent.message_sent
&event_prefix=integration.
&actor_id=
&from=ISO8601
&to=ISO8601
&limit=100
&cursor=
```

**Response 200:**

```json
{
  "items": [ { "...AuditEvent" } ],
  "next_cursor": "opaque"
}
```

Requires permission `audit.read`.

### GET `/api/v1/audit/events/{id}`

Single event detail.

### POST `/api/v1/internal/audit/emit`

Service-to-service. mTLS + service token.

**Body:** partial AuditEvent without `id`, `created_at`.

## Token usage

### GET `/api/v1/tenants/{tenant_id}/usage/tokens`

**Query:** `?from=&to=&group_by=day|cabinet|model`

**Response:**

```json
{
  "groups": [
    {
      "key": "2026-08-20",
      "input_tokens": 120000,
      "output_tokens": 45000,
      "estimated_cost_usd": 12.50
    }
  ],
  "total": { "input_tokens": 120000, "output_tokens": 45000 }
}
```

### GET `/api/v1/cabinets/{cabinet_id}/usage/tokens`

Cabinet-scoped subset.

### GET `/api/v1/projects/{project_id}/usage/tokens`

Project drill-down.

## MCP metrics (Prometheus)

### GET `/metrics`

Platform scrape endpoint (not tenant-authenticated).

See [mcp-tools.md](mcp-tools.md) for metric names.

## Retention admin

### GET `/api/v1/platform/retention/policies`

platform.admin

### PATCH `/api/v1/platform/retention/policies/{data_type}`

### POST `/api/v1/platform/retention/run`

Trigger manual retention job (dry_run optional).

## Export

### POST `/api/v1/tenants/{tenant_id}/audit/export`

**Body:** `{ "from", "to", "format": "jsonl" | "csv" }`

**Response 202:** `{ "export_id", "status_url" }`

Async → download link in object storage (24h TTL).

## Error codes

| code | HTTP |
| --- | --- |
| `AUDIT_QUERY_TOO_WIDE` | 400 |
| `EXPORT_IN_PROGRESS` | 409 |
| `USAGE_PERIOD_TOO_LONG` | 400 |
