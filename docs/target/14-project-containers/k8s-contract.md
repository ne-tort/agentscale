# Project Containers — Kubernetes contract

## Mapping

| Platform | Kubernetes |
|----------|------------|
| `ProjectContainer.id` | label `prodavan.io/container-id` |
| `Project.id` | label `prodavan.io/project-id` |
| `company_id` / `cabinet_id` | labels (index / NetworkPolicy later) |
| `runtime_ref` | Pod name (or Job name) + uid |
| workspace | PVC **or** emptyDir+hydrate (MVP profile documented per env) |

Namespace: dedicated `prodavan-sandboxes` (рекомендация) или тот же cluster ns с жёсткими labels — зафиксировать в overlay; **не** смешивать с Traefik/Argo без labels.

## Workload shape (целевой MVP P3)

- **Pod** (long-running) или **Job** with restart policy — выбрать long-running Pod для agent cwd.
- Resources: requests/limits CPU+memory (profile from company policy later; default platform profile).
- Probes: readiness on workspace ready; liveness conservative.
- Image: platform sandbox image (agent tools + network); **не** prodavan-api image.

## Labels / selectors (обязательные)

```text
app.kubernetes.io/part-of: prodavan
app.kubernetes.io/component: project-container
prodavan.io/container-id: pctr_…
prodavan.io/project-id: proj_…
prodavan.io/managed-by: container-runtime
```

Только модуль 14 создаёт/удаляет объекты с `managed-by=container-runtime`.

## Metrics (собирать через Port.get_metrics)

| Source | Fields |
|--------|--------|
| Pod status | phase, reason, restarts, conditions (Ready, ContainersReady) |
| metrics.k8s.io | cpu/memory usage vs limits |
| PVC / ephemeral | used bytes / capacity when available |
| Node (optional) | not required for MVP admin UI |

Связанные **не-k8s** метрики (tokens, messages) — join из `agent_usage` / sessions по `project_id` в admin read-model (не в Port).

## Zombie detection

**Zombie** = объект k8s с `managed-by=container-runtime`, для которого:

- нет строки `ProjectContainer`, или
- Project `deleted` / отсутствует, или
- `runtime_ref` в DB указывает другой uid, а старый pod жив

`list_orphans` + `reconcile` (Celery beat / admin sweep): force delete + audit `container.zombie_reaped`.

## As-is probe (не путать)

Существующий PVC probe Job / `SANDBOX_K8S_JOBS` ([L07](../12-layer-docs/L07-projects-runtime.md)) — **инфра-проверка**, не Project Container. Не использовать как runtime агента.
