# k3s Runtime — операции lifecycle

Маппинг доменных операций `PodCommand` → k8s API. SoT: [`lifecycle.md`](../lifecycle.md).

## Таблица операций

| Domain op | `project_pods.desired_state` | `status` (transient) | k8s action | Events |
|-----------|------------------------------|----------------------|------------|--------|
| Lazy start (trigger) | `running` | `provisioning` → `running` | Create Pod + hydrate | `pod.started`, `pod.hydrated` |
| Project resume | `running` | `provisioning` | Create Pod (new UID) + hydrate | `pod.started`, `pod.hydrated`, `project.resumed` |
| Project pause | `paused` | `paused` | Delete Pod (grace 30s) | `pod.paused`, `project.paused` |
| Soft delete project | `terminated` | `terminated` | Delete Pod (grace 0) | `pod.terminated` |
| Purge | row deleted | — | Delete Pod if exists | `pod.terminated` |
| Failed recovery | `running` | `provisioning` | Delete failed Pod + recreate | `pod.failed` → `pod.started` |
| Reconcile drift | unchanged | fix actual | Delete orphan / create missing | optional `pod.*` |

## Create Pod (`ensure_running`)

**Preconditions:**

- `project_pods` row exists, `desired_state=running`
- `project.status` in `{active, running}` (not paused/deleted)
- Quota OK (company layer)

**Steps:**

1. `GET Pod/{runtime_ref}` — if `Running|Pending`, idempotent return
2. Build PodSpec from [k8s-contract.md](../k8s-contract.md)
3. `POST Pod` in namespace `prodavan-sandboxes`
4. Wait Ready (readiness probe on `/health` or exec check) — timeout → `failed`
5. `HydratePort.hydrate(...)` — see [file-sync.md](file-sync.md)
6. Update row: `status=running`, `runtime_uid`, `last_started_at`
7. Emit `pod.started`, `pod.hydrated`

**Idempotency key:** `runtime_ref` (stable per project pod row). Duplicate create → catch `409 AlreadyExists`.

## Pause (`pause`)

Pause = **delete Pod**, not scale to 0 Deployment (1:1 Pod model).

1. `DELETE Pod/{runtime_ref}?gracePeriodSeconds=30`
2. Ignore `404`
3. Row: `status=paused`, clear `runtime_uid` (optional keep ref name)
4. Emit `pod.paused` **before** `project.paused` (ordering in `PodCommand`)

## Terminate (`terminate`)

Hard delete for purge / soft-delete cascade:

1. `DELETE Pod/{runtime_ref}?gracePeriodSeconds=0`
2. Row: `status=terminated` or delete row on purge
3. Emit `pod.terminated`

## Force kill

Admin / stuck Pod:

1. `DELETE` with `gracePeriodSeconds=0`
2. If still terminating: patch `metadata.finalizers` (last resort — document in runbook)
3. Reconcile will recreate if `desired=running`

## get_status / runtime summary

`PodQuery.runtime_summary` merges:

| Field | Source |
|-------|--------|
| `desired_state`, `status` | PostgreSQL |
| `phase`, `restarts` | k8s Pod status |
| `last_event_at` | outbox / row |

On `POD_RUNTIME_MODE=stub` — synthetic phase from row status.

## Reconcile worker

Periodic (Celery beat, ~60s):

```text
FOR each project_pods WHERE desired=running AND status IN (running, provisioning, failed):
  actual = get_status(runtime_ref)
  IF actual NotFound AND desired=running → ensure_running
  IF actual Running AND desired=paused → pause (fix drift)
  IF actual Failed AND status=running → mark failed OR recreate (policy flag)

FOR each Pod in k8s with label managed-by=pod-service:
  IF no matching project_pods row → delete (zombie)
```

Zombie policy: [k8s-contract.md](../k8s-contract.md).

## RBAC required (API SA)

Minimum rules for `pod-service` SA — см. [gitops-rbac.md](gitops-rbac.md):

- `pods`: create, get, list, watch, delete, patch
- `pods/exec`: create, get (WebSocket exec for files port; GET upgrade requires get)
- `jobs`: create, get, delete (hydrate job mode)
- `pods/metrics` or `metrics.k8s.io`: get (metrics port)
