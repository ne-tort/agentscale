# 14 — Project Containers

Изолированный **k8s Pod** на каждый Project + workspace в object store.

| | |
|--|--|
| Сущность | `ProjectContainer` (1:1 Project) |
| Compute | Pod (не «логический object-ws») |
| Файлы | MinIO `projects/{workspace_key}/` → hydrate в `/workspace` |
| Writer k8s | только этот модуль (`ContainerRuntimePort`) |
| Иерархия | [00-entities](../00-entities.md) |

## Документы

| Файл | Содержание |
|------|------------|
| [pod-service.md](pod-service.md) | BC `pod_service`: границы, API, events, Relations |
| [domain.md](domain.md) | Сущность, статусы, Port (transitional → 1:1) |
| [lifecycle.md](lifecycle.md) | create / pause / resume / delete |
| [isolation.md](isolation.md) | NetworkPolicy, SA, peer |
| [k8s-contract.md](k8s-contract.md) | labels, resources, zombies |
| [errors-ops.md](errors-ops.md) | stuck / force-kill / audit |
| [admin-ui.md](admin-ui.md) | Admin «Контейнеры» |
| [adr.md](adr.md) | Решения (Pod, pause=delete Pod, MinIO SoT) |

**План реализации:** [P1-pod-service](../11-implementation-plan/P1-pod-service.md) (Phase 1–4, PR-разрез, DoD).

## Non-goals

- Starter `cabinet.bundle` (упаковка кабинетов)
- Cabinet CRUD / meta DDL
- Agent SDK (модуль 08)
- PVC probe Job ≠ runtime агента

## Долг кода (не канон)

Код пока может держать только object-ws без Pod — это **недоделка**, не целевая модель. Цель = Pod. См. [09-gap-map](../09-gap-map.md).
