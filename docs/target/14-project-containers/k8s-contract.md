# Project Pod — Kubernetes contract

Runtime objects for **Project Pod** (1:1 Project). Writer: `pod_service` only.

## Mapping

| Platform | k8s |
|----------|-----|
| `project_pods.id` | label `prodavan.io/pod-id` |
| `Project.id` | label `prodavan.io/project-id` |
| `Company.id` | label `prodavan.io/company-id` |
| workspace | hydrate → `/workspace` (emptyDir + initContainer or Job) |
| `runtime_ref` | Pod metadata.name (stable) + `.metadata.uid` (per instance) |

Namespace: **`prodavan-sandboxes`** (dedicated; not `default`).

Подробнее: [k3s-runtime/](k3s-runtime/).

## Workload

- Long-running **Pod** (agent cwd = `/workspace`).
- Image: `prodavan-sandbox` (tools + network), **не** image API.
- `resources.requests/limits` — CPU + memory (company quota profile).
- Readiness: workspace hydrated / health endpoint.

Deployment/ReplicaSet **не** используются — один Pod на project row.

## Labels

```text
app.kubernetes.io/part-of: prodavan
app.kubernetes.io/component: project-pod
prodavan.io/managed-by: pod-service
prodavan.io/pod-id: <uuid>
prodavan.io/project-id: <uuid>
prodavan.io/company-id: <uuid>
prodavan.io/workspace-key: <key>
```

**Selector для reconcile / zombies:**

```text
prodavan.io/managed-by=pod-service
```

### Deprecated (удалить из кода и manifests)

```text
prodavan.io/container-id / pctr_*
prodavan.io/managed-by: container-runtime
app.kubernetes.io/component: project-container
```

## Metrics

- Pod phase, restarts, conditions — `PodMetricsPort` / runtime summary API.
- CPU/RAM — metrics-server (`metrics.k8s.io`).
- Agent tokens/messages — join `agent_usage` по `project_id` (не k8s).

См. [k3s-runtime/metrics-observability.md](k3s-runtime/metrics-observability.md).

## Zombies

Pod с `managed-by=pod-service` без строки `project_pods` или при `desired_state=terminated` / deleted project → **delete** в reconcile.

PVC probe Job (`SANDBOX_K8S_JOBS`) — **не** project runtime Pod.

## RBAC

API ServiceAccount — Role в `prodavan-sandboxes`: pods CRUD, exec, jobs, metrics read.  
См. [k3s-runtime/gitops-rbac.md](k3s-runtime/gitops-rbac.md).
