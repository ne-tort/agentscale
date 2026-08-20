# M09 — Доменная модель

## AuditEvent

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "cabinet_id": "uuid",
  "actor_id": "uuid",
  "actor_type": "user",
  "event_type": "integration.policy_updated",
  "resource_type": "integration_policy",
  "resource_id": "uuid",
  "payload": {},
  "ip_address": "203.0.113.1",
  "user_agent": "Prodavan/1.0",
  "created_at": "2026-08-20T10:00:00Z"
}
```

**actor_type:** `user` | `system` | `agent` | `platform`

### event_type taxonomy

Prefix by module:

| prefix | examples |
| --- | --- |
| `tenant.` | `tenant.created`, `tenant.hard_deleted` |
| `auth.` | `auth.login`, `auth.login_failed` |
| `integration.` | `integration.policy_updated`, `integration.s4b_credentials_rotated` |
| `mcp.` | `mcp.server_enabled`, `mcp.tool_call` |
| `agent.` | `agent.message_sent`, `agent.run_cancelled`, `agent.session_reset` |
| `platform.` | `platform.break_glass_access` |

### payload rules

- No secrets, passwords, tokens
- PII minimized — email as `user_id` reference
- Large payloads → `payload_ref` to cold storage

## TokenUsageRecord

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "cabinet_id": "uuid",
  "project_id": "uuid",
  "session_id": "uuid",
  "run_id": "uuid",
  "model": "claude-sonnet-4",
  "provider": "anthropic",
  "input_tokens": 4500,
  "output_tokens": 1200,
  "cache_read_tokens": 0,
  "cache_write_tokens": 0,
  "estimated_cost_usd": 0.042,
  "created_at": "2026-08-20T10:05:00Z"
}
```

### Aggregation windows

| grain | table/view |
| --- | --- |
| run | raw record |
| day | `token_usage_daily` materialized |
| month | billing export |

## McpMetricSample (conceptual)

Not stored long-term — Prometheus scrape. Documented names below.

## RetentionPolicy

```json
{
  "data_type": "audit_events",
  "default_days": 365,
  "starter_days": 90,
  "enterprise_days": 2555,
  "archive_before_delete": true
}
```

## Retention defaults

| data_type | default retention | archive |
| --- | --- | --- |
| `audit_events` | 365 days | S3 cold |
| `integration_call_log` | 90 days | no |
| `agent.stream_events` | 90 days | optional |
| `agent.messages` | 365 days | no |
| `token_usage` | 730 days | no |
| `mcp.session_bindings` | 30 days | no |
| `tenants.refresh_tokens` | until expiry + 7d | no |

## Audit query filters

```json
{
  "tenant_id": "required",
  "cabinet_id": "optional",
  "event_types": ["agent.*"],
  "actor_id": "optional",
  "from": "2026-08-01T00:00:00Z",
  "to": "2026-08-20T23:59:59Z",
  "limit": 100
}
```

## Billing hook

Daily job aggregates `token_usage` → `billing_usage_lines` for Stripe/manual invoice.
