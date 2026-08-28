# ProjectPod — domain

> **As-built (P1):** 1:1 `ProjectPod` per Project in [`pod_service`](pod-service.md).  
> Legacy `ProjectRuntimeUnit` (0..N) удалён из кода и схемы — см. миграции `2026082818` / `2026082819`.

## Сущность

`ProjectPod` — учётная запись **изолированного runtime** (ровно один live pod на active/paused Project).

| Поле | Смысл |
|------|--------|
| `id` | `pod_*` |
| `project_id` | FK → Project, UNIQUE для live row |
| `workspace_key` | denorm для hydrate |
| `status` | `pending` \| `provisioning` \| `running` \| `pausing` \| `paused` \| `failed` \| `terminating` \| `terminated` |
| `desired_state` | `absent` \| `running` |
| `runtime_ref` | opaque ref (`object-ws:{key}` stub; k8s Pod name позже) |
| `last_error` | последняя ошибка оркестратора |
| `hydrate_generation` | bump on rematerialize |

Project хранит legacy `container_ref` (transitional mirror of `runtime_ref`).

Pod создаётся **lazy** — при первом agent/trigger или при resume после pause.

## Статусы ↔ Project

| Project.status | Pod (желаемое) |
|----------------|----------------|
| `active` | `desired_state=running` (lazy или после resume) |
| `paused` | `desired_state=absent`, Pod paused |
| `completed` | `desired_state=absent` |
| `deleted` | Pod terminated |

## События

`pod.provisioned`, `pod.started`, `pod.hydrated`, `pod.paused`, `pod.resumed`, `pod.terminated`, `pod.failed`, `pod.reconciled` — platform bus.

Первый lazy start: `pod.started` → `pod.hydrated` → `project.started`. Resume: `pod.resumed` → `project.resumed`.

## API

Employee UI — только через Project lifecycle (`pause`/`resume`/`delete`). Admin — `GET /admin/containers`, force-kill, reconcile.
