# Project Containers — gap from as-is

## Сегодня (код)

| Area | Fact |
|------|------|
| Identity | `container_ref = object-ws:{workspace_key}` (+ legacy `local-ws:`) |
| Create | Materialize FS + MinIO dual-write; **no Pod** |
| Pause | Cancel sessions + `pause_container` **no-op** (`pod_stop: false`) |
| Resume | AI key gate + Project ACTIVE; **no start_container** |
| Delete | Soft-delete + wipe tree |
| K8s | PVC probe Job only (`SANDBOX_K8S_JOBS`, admin endpoints); isolator `not_wired` |
| Owner | `ProjectService` owns lifecycle; no Container BC |
| Admin UI | P1: tabs Контейнеры + Кабинеты stub (Bundles chrome removed); starter-bundle API remains for employee import |

## Цель (канон 14)

| Area | Target |
|------|--------|
| Entity | `ProjectContainer` 1:1 Project |
| Owner | `ContainerRuntimePort` sole k8s writer |
| Runtime | Per-project Pod + NetworkPolicy + hydrate |
| Admin | Tabs Контейнеры + Кабинеты stub; no Bundles chrome |

## Reuse

- `workspace_key` / object-ws blob layout ([container.md](../06-projects-runtime/container.md) layout)
- Materialize + wipe
- Project pause/resume/delete + key cascade
- Probe Job SA patterns (как образец RBAC, не как runtime)

## Drop / move

- Admin `AdminStarterBundlesPage` from shell (catalog API may remain for employee)
- Treating `pause_container` stub as «isolator done»
- Any doc implying Bundles = project containers

## Phase bridge

```text
P1 UI read-model (Project as proxy)
  → P2 ProjectContainer row + Port stub (object-ws)
  → P3 Pod isolator
  → P4 rich metrics UI
```
