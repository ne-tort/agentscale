# k3s Runtime — метрики и observability

## Уровни наблюдаемости

| Layer | What | Consumer |
|-------|------|----------|
| **Domain** | `project_pods.status`, `pod.*` Kafka | UI project card, automations |
| **Runtime** | Pod phase, restarts, conditions | `PodQuery`, reconcile |
| **Resource** | CPU, memory, ephemeral storage | Admin quotas, alerts |
| **Product** | agent tokens, session duration | metrics BC (L09) |

k3s layer отвечает за **runtime + resource**; product metrics остаются в существующем pipeline.

## PodMetricsPort (read-only)

### metrics-server (default k3s)

Requires `metrics-server` in cluster (k3s ships it).

```text
GET /apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods/{name}
→ cpu.usageNanoCores, memory.usageBytes
```

Adapter normalizes to:

```json
{
  "cpu_millicores": 120,
  "memory_bytes": 536870912,
  "timestamp": "2026-08-28T12:00:00Z"
}
```

### Pod status (native)

From Pod `.status`:

| Field | Use |
|-------|-----|
| `phase` | Running / Pending / Failed / Succeeded |
| `containerStatuses[].restartCount` | Flapping detection |
| `conditions[]` | Ready, PodScheduled |
| `startTime` | Uptime |

Expose via `GET /projects/{id}/runtime` (existing endpoint) — extend DTO in P2.

## Reconcile metrics (platform)

Instrument `PodReconcileService`:

| Metric | Type |
|--------|------|
| `pod_reconcile_runs_total` | counter |
| `pod_reconcile_drift_fixed_total` | counter by action |
| `pod_reconcile_zombies_deleted_total` | counter |
| `pod_reconcile_duration_seconds` | histogram |

Prometheus scrape from API worker metrics endpoint ([13-platform-infra](../../13-platform-infra/)).

## Logging

Structured logs on every k8s call:

```text
k8s_op=create_pod runtime_ref=pod-ws-abc project_id=... duration_ms=... result=ok|error
```

No secret values in logs.

## Tracing (optional P2+)

OpenTelemetry span per `sync_desired` with child spans for k8s API calls — same trace id as HTTP request.

## Admin UI

`/admin/containers` (existing read service) — batch `list_managed_pods()` + optional metrics batch.

Sort/filter by: company, phase, restarts, last_started_at.

## Prodavan Metrics BC (in prodavan-api)

Cluster addon **metrics-server** supplies raw CPU/RAM. Prodavan does **not** deploy a separate metrics microservice.

```text
PodReconcileService → PodMetricsSampler → metrics-server (read)
  → Kafka prodavan.metrics.events (pod.metrics.sample | pod.metrics.degraded)
  → MetricsConsumerResource → Redis (latest + series)
  → MetricsQuery → REST / containers / runtime
```

Verify on dev: [`docs/07-infrastructure/wsl-dev.md`](../../../07-infrastructure/wsl-dev.md#k8s-metrics-server).

## Alerts (target)

| Condition | Action |
|-----------|--------|
| `status=failed` > 5 min | `pod.failed` already emitted; alert webhook |
| restartCount > N / hour | mark degraded, optional auto-recreate |
| zombie Pods | reconcile deletes + metric increment |

## Not in scope P2

- kube-state-metrics full export (can add later for Grafana dashboards)
- cAdvisor direct scrape (metrics-server sufficient)
- In-Pod Prometheus sidecar
