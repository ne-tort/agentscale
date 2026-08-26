# ProjectContainer — lifecycle

## Каскад

```text
Admin / key disable / idle
  → ProjectService.pause|resume|delete
    → ContainerRuntimePort.pause|start|delete
```

## Операции

| Op | Project | Container |
|----|---------|-----------|
| **create** | insert, materialize workspace → MinIO | `ensure` + `start` (Pod + hydrate) |
| **pause** | status=paused; cancel sessions | sync workspace → MinIO; **delete Pod**; status=`paused` |
| **resume** | AI key gate; status=active | **new Pod** + hydrate from MinIO; status=`running` |
| **delete** | soft-delete | wipe MinIO + delete Pod |
| **force-kill** | опционально paused/failed | grace=0 Pod delete |

## Почему pause ≠ «замороженный Pod»

Paused Pod держит RAM requests. При многих спящих проектах это дорого.  
Канон: **compute off**, файлы в MinIO (дешёвое хранение). Image слои Docker общие на нодах — не копируются на каждый проект.

Resume медленнее (create Pod + hydrate), зато масштабируется.

## Desired state / reconcile

Источник желаемого: `Project.status` (+ admin force-kill).  
Drift (Project paused, Pod ещё Running) → Port.pause.  
Zombie Pod (нет Project / deleted) → force_kill + audit.

Key re-enable **не** auto-resume Project.
