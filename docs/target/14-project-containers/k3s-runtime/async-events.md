# k3s Runtime — async: Kafka, Celery, idempotency

## Разделение sync vs async

```text
┌──────────────────┐     sync      ┌─────────────┐
│ project_service  │ ──────────────► │ PodCommand  │
└──────────────────┘                 └──────┬──────┘
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    │ sync                │ async                 │
                    ▼                     ▼                       ▼
             K8sPodRuntimeAdapter   PG outbox              Celery reconcile
             HydratePort            → Kafka pod.*          (periodic drift)
                                    → Relations bind
```

**Anti-pattern:** `pod.create` Kafka topic consumed by operator — duplicates command path, race with PG transaction.

## Kafka events (whitelist)

From [pod-service.md](../pod-service.md):

| Event | When | Payload hints |
|-------|------|---------------|
| `pod.started` | Pod Ready + row running | `pod_id`, `project_id`, `runtime_ref`, `runtime_uid` |
| `pod.hydrated` | Workspace sync done | `hydrate_generation` |
| `pod.paused` | Pod deleted, paused | |
| `pod.terminated` | Pod deleted, terminated | |
| `pod.failed` | Unrecoverable error | `error_code`, `message` |

Consumers (examples):

- Metrics aggregator — presence / active sandboxes
- Audit log
- Future: billing meter (compute seconds)

## Outbox pattern

`PodLifecycleEmitter` writes to transactional outbox in same DB transaction as `project_pods` update → relay to Kafka (existing platform pattern).

**Idempotency:** consumers use `(event_id)` or `(pod_id, event_type, hydrate_generation)`.

## Celery tasks

| Task | Schedule | Purpose |
|------|----------|---------|
| `pod.reconcile_all` | 60s | Drift + zombies |
| `pod.recover_failed` | on-demand / 5m | Retry failed with backoff cap |

Tasks call `PodReconcileService`, not k8s directly — same as API path.

## Hydrate async completion

If hydrate = Batch Job:

1. `PodCommand` creates Job, sets `status=provisioning`
2. Job completion watcher (Celery poll or k8s watch in worker):
   - success → `status=running`, emit `pod.hydrated`
   - failure → `status=failed`, emit `pod.failed`

**Watch vs poll:** P2 use poll in reconcile (simpler); upgrade to `kubernetes.watch` in dedicated worker if latency matters.

## Relations

On `pod.started`:

```text
RelationsCommand.bind_pod(project_id, pod_id, runtime_ref)
→ relation.pod_bound Kafka
```

On terminate: `unbind_pod`.

Relations — audit graph, not runtime control.

## Failure handling

| Failure point | Row state | Recovery |
|---------------|-----------|----------|
| k8s create timeout | provisioning | reconcile retry |
| hydrate job failed | failed | manual or auto recreate |
| Kafka down | outbox backlog | relay catches up |
| API crash mid-sync | provisioning | reconcile |

Use `project_pods.updated_at` + stale threshold to detect stuck provisioning.
