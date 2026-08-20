# M09 — MCP metrics (Prometheus)

Endpoint: `GET /metrics` (platform scrape, auth via network policy or bearer).

## Naming convention

Prefix: `prodavan_`  
Subsystem labels: `integration`, `mcp`, `agent`, `audit`

## MCP metrics (canonical names)

### Discovery & lifecycle

| metric | type | labels | description |
| --- | --- | --- | --- |
| `prodavan_mcp_discovery_duration_seconds` | histogram | `server_id`, `cabinet_id`, `status` | tools/list latency |
| `prodavan_mcp_installations_total` | gauge | `server_id`, `state` | count by state |
| `prodavan_mcp_discovery_failures_total` | counter | `server_id`, `error_code` | failed discoveries |
| `prodavan_mcp_process_restarts_total` | counter | `server_id`, `installation_id` | supervisor restarts |

### Tool calls

| metric | type | labels | description |
| --- | --- | --- | --- |
| `prodavan_mcp_tool_calls_total` | counter | `server_id`, `tool`, `status`, `cabinet_id` | ok/error/rate_limited |
| `prodavan_mcp_tool_call_duration_seconds` | histogram | `server_id`, `tool` | execution time |
| `prodavan_mcp_tools_exposed` | gauge | `profile_id`, `cabinet_id` | effective tool count |
| `prodavan_mcp_active_processes` | gauge | `server_id` | running MCP processes |

### Integration (M05) — cross-ref

| metric | type | labels |
| --- | --- | --- |
| `prodavan_integration_calls_total` | counter | `integration`, `operation`, `status`, `cabinet_id` |
| `prodavan_integration_call_duration_seconds` | histogram | `integration`, `operation` |
| `prodavan_integration_rate_limit_hits_total` | counter | `integration`, `cabinet_id` |

## Agent metrics

| metric | type | labels |
| --- | --- | --- |
| `prodavan_agent_runs_total` | counter | `status`, `model`, `cabinet_id` |
| `prodavan_agent_run_duration_seconds` | histogram | `model` |
| `prodavan_agent_stream_events_total` | counter | `event_type` |
| `prodavan_agent_tokens_total` | counter | `model`, `direction` | direction=input/output |

## Token usage (billing)

| metric | type | labels |
| --- | --- | --- |
| `prodavan_llm_tokens_total` | counter | `tenant_id`, `model`, `direction` |
| `prodavan_llm_estimated_cost_usd_total` | counter | `tenant_id`, `model` |

**Cardinality warning:** `tenant_id` on metrics — allowlist for enterprise debug only; default aggregate without tenant label in shared Prometheus.

## Audit metrics

| metric | type | labels |
| --- | --- | --- |
| `prodavan_audit_events_total` | counter | `event_type` |
| `prodavan_audit_emit_failures_total` | counter | `reason` |
| `prodavan_retention_rows_deleted_total` | counter | `data_type` |

## Example PromQL

```promql
# MCP tool error rate 5m
sum(rate(prodavan_mcp_tool_calls_total{status="error"}[5m]))
  / sum(rate(prodavan_mcp_tool_calls_total[5m]))

# p95 s4b search latency
histogram_quantile(0.95,
  sum(rate(prodavan_integration_call_duration_seconds_bucket{integration="s4b"}[5m])) by (le)
)

# Token burn per hour
sum(increase(prodavan_llm_tokens_total{direction="output"}[1h])) by (model)
```

## Grafana dashboards

| dashboard uid | title |
| --- | --- |
| `prodavan-mcp-overview` | MCP Health |
| `prodavan-integrations` | S4B & Web Shops |
| `prodavan-agent-usage` | Agent Runs & Tokens |
| `prodavan-audit-ops` | Audit Volume |

## MCP query tools (prodavan-operations server)

Read-only, `audit.read` permission:

### `operations.query_audit`

**Args:** `event_prefix`, `from`, `to`, `limit`

### `operations.usage_summary`

**Args:** `period_days` (default 30)

### `operations.metric_hints`

Returns documented metric names for admins.

## Alert rules (starter)

```yaml
- alert: McpDiscoveryFailureSpike
  expr: increase(prodavan_mcp_discovery_failures_total[15m]) > 5
  for: 5m

- alert: IntegrationRateLimitHigh
  expr: rate(prodavan_integration_rate_limit_hits_total[5m]) > 1
  for: 10m

- alert: AuditEmitFailures
  expr: increase(prodavan_audit_emit_failures_total[5m]) > 0
  for: 1m
```

## Commerce → Prodavan metrics mapping

| Commerce (informal) | Prodavan |
| --- | --- |
| bot logs tool calls | `prodavan_mcp_tool_calls_total` |
| manual token tracking | `operations.token_usage` table + counters |
| none | full audit_events schema |
