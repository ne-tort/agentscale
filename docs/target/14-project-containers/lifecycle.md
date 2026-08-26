# Project Containers — lifecycle

## Кто кого вызывает

```text
Admin UI (Контейнеры)
  └─▶ ProjectService.pause | resume | delete
         └─▶ ContainerRuntimePort.pause | start | delete

Key disable cascade
  └─▶ ProjectService.pause
         └─▶ ContainerRuntimePort.pause

Idle pause / company suspend
  └─▶ ProjectService.pause / stop_company_runtime
         └─▶ (per project) ContainerRuntimePort.pause
```

**Запрещено:** key → ContainerPort напрямую; ContainerPort → delete Company; UI → raw kubectl.

## Операции

| Op | Project side | Container side |
|----|--------------|----------------|
| **create project** | insert Project, materialize | `ensure_created` (workspace ready; P3: schedule pod) |
| **pause** | status=paused; cancel ACTIVE sessions | `pause` (stop compute; keep volume/blobs) |
| **resume** | require valid AI key; status=active; drain triggers | `start` (only after Project gate OK) |
| **delete** | soft-delete; wipe workspace policy | `delete` (+ wipe if purge) |
| **force-kill** | optional project stay paused/failed | `force_kill` then reconcile |
| **reconcile** | read desired from Project.status | list orphans; fix drift |

## Desired state

Источник желаемого состояния для Container — **Project.status** (+ explicit admin force-kill).

| Desired | Action if drift |
|---------|-----------------|
| Project active, Container paused | `start` (если resume path уже прошёл gate) |
| Project paused, Container running | `pause` |
| Project deleted, Pod exists | `delete` / force |
| Pod exists, no Project / deleted | zombie → `force_kill` + audit |

Reconcile **не** auto-resume Project при появлении ключа (см. [02 domain](../02-ai-provider-keys/domain.md)).

## Create path (целевой)

```text
1. ProjectService.create
2. Materialize workspace → object store (13)
3. ContainerRuntimePort.ensure_created(project_id, workspace_key)
4. (P3) Create Pod/Job with hydrate init + NetworkPolicy
5. Store runtime_ref on ProjectContainer (+ opaque on Project)
```

## Pause path (уже частично в коде)

```text
ProjectService.pause
  → stop_project_runtime (sessions)
  → ContainerRuntimePort.pause   # today: pause_container no-op stub
```

Целевой stub остаётся в модуле 14; `application/projects/container_lifecycle.py` → thin adapter to Port.

## Resume path

```text
ProjectService.resume
  → resolve_credentials (NO_AI_KEY → abort, project stays paused)
  → Project ACTIVE
  → ContainerRuntimePort.start
```

## Delete path

```text
ProjectService.delete(purge_workspace=…)
  → ContainerRuntimePort.delete(wipe=purge)
  → soft-delete Project
```

## Force-kill / zombies

См. [errors-ops.md](errors-ops.md), [k8s-contract.md](k8s-contract.md). Admin detail: действие «Force kill» → Port.force_kill → audit `container.force_killed`.
