# ProjectContainer — Kubernetes contract

## Mapping

| Platform | k8s |
|----------|-----|
| `ProjectContainer.id` | label `prodavan.io/container-id` |
| `Project.id` | label `prodavan.io/project-id` |
| workspace | hydrate → `/workspace` (PVC или emptyDir+copy) |
| `runtime_ref` | Pod name + uid |

Namespace: рекомендуется `prodavan-sandboxes` (или тот же ns с жёсткими labels).

## Workload

- Long-running **Pod** (cwd агента).
- Image: sandbox (tools + network), **не** image API.
- requests/limits CPU+memory.
- readiness: workspace ready.

## Labels

```text
app.kubernetes.io/part-of: prodavan
app.kubernetes.io/component: project-container
prodavan.io/container-id: pctr_…
prodavan.io/project-id: proj_…
prodavan.io/managed-by: container-runtime
```

Только модуль 14 создаёт/удаляет объекты с `managed-by=container-runtime`.

## Metrics

Pod phase/restarts; metrics-server CPU/RAM; PVC usage если есть.  
Tokens/messages — join `agent_usage` по `project_id` (не k8s).

## Zombies

Pod с `managed-by=container-runtime` без живого Project / при Project deleted → reap.

PVC probe Job (`SANDBOX_K8S_JOBS`) — **не** ProjectContainer.
