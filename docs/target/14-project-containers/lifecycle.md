# ProjectContainer — lifecycle

## Каскад

```text
Admin / key disable / idle
  → ProjectService.pause|resume|delete
    → ContainerRuntimePort.pause|start|delete
```

## Операции

| Op | Project | Container | Agent sessions |
|----|---------|-----------|----------------|
| **create** | insert, materialize workspace → MinIO | `ensure` + `start` (Pod + hydrate) | — |
| **pause** | status=paused | **delete Pod** (grace=30); workspace blobs in MinIO unchanged | **SUSPENDED** (reactivate on resume) |
| **complete** | status=completed | delete Pod | **SUSPENDED** |
| **resume** | AI key gate; status=active | **new Pod** + hydrate from MinIO | reactivate + bridge bootstrap |
| **reload** | status=active | terminate + recreate Pod (no rematerialize) | reactivate + bridge bootstrap |
| **sync** («Обновить проект») | clears `workspace_outdated_at` | materialize workspace; bump hydrate_generation | bridge bootstrap if running |
| **soft_delete** (archive) | status=deleted; **blobs keep** | **delete Pod** | **SUSPENDED** |
| **soft_delete** + `purge_workspace` | wipe MinIO | delete Pod | **CANCELLED** |
| **purge** | after soft_delete; wipe MinIO | ensure Pod gone | **CANCELLED** (ACTIVE + SUSPENDED) |
| **force-kill** | опционально paused/failed | grace=0 Pod delete | — |

`inert` (paused **или** soft_deleted) ⇒ desired Pod = absent. См. [00-lifecycle.md](../00-lifecycle.md).

## Deferred workspace sync

По умолчанию `projects_auto_rematerialize_on_cabinet_change=false`.

Изменения модулей кабинета (CRUD строк, bind/unbind) **не** rematerialize Pod сразу — выставляют `workspace_outdated_at` на затронутых проектах. Пользователь применяет изменения явно через **«Обновить проект»** (`POST /projects/{id}/sync`).

API ответы содержат `workspace_sync` (`mode: deferred|scheduled`, `marked_outdated`, `scheduled`); ключ `rematerialize` сохранён как alias.

## Почему pause ≠ «замороженный Pod»

Paused Pod держит RAM requests. При многих спящих проектах это дорого.  
Канон: **compute off**, файлы в MinIO (дешёвое хранение). Image слои Docker общие на нодах — не копируются на каждый проект.

Resume медленнее (create Pod + hydrate), зато масштабируется.

## Desired state / reconcile

Источник желаемого: `Project.status` (+ admin force-kill).  
Drift (Project paused, Pod ещё Running) → Port.pause.  
Zombie Pod (нет Project / deleted) → force_kill + audit.

Key re-enable **не** auto-resume Project.
