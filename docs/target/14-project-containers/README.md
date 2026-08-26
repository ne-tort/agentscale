# 14 — Project Containers

| Поле | Значение |
|------|----------|
| Status | canon (docs); code — hole (object-ws transitional) |
| Priority | after P0 infra blobs; before claiming «k8s isolator done» |
| Depends on | [06 Projects](../06-projects-runtime/), [13 Platform infra](../13-platform-infra/), [05 Cabinets](../05-cabinets/) |
| Non-goals | Starter cabinet bundles; Cabinet CRUD; Agent SDK |

## Суть

**Project Container** — независимый bounded context: сущность runtime-изоляции проекта и **единственный** writer в Kubernetes для project sandboxes.

`Project` и `ProjectContainer` связаны иерархией (1:1 MVP), но **не** смешиваются:

- Project = work unit (status, triggers, chat, AI key gate).
- Container = compute + workspace mount lifecycle в k8s (+ object-ws hydrate).

Admin UI «Контейнеры» управляет **проектом через каскад** (pause/resume/delete → ProjectService → ContainerPort). Не key→container напрямую.

## Документы модуля

| Файл | Содержание |
|------|------------|
| [domain.md](domain.md) | Сущность, статусы, иерархия, границы |
| [lifecycle.md](lifecycle.md) | create/start/pause/resume/delete/force-kill/reconcile; каскад |
| [isolation.md](isolation.md) | NetworkPolicy, SA, volumes, peer isolation |
| [k8s-contract.md](k8s-contract.md) | Pod/Job/PVC, labels, metrics, zombies |
| [errors-ops.md](errors-ops.md) | Stuck pods, force delete, audit |
| [admin-ui.md](admin-ui.md) | List/detail, columns, actions |
| [gap-from-as-is.md](gap-from-as-is.md) | object-ws today → Pod target |
| [adr-independence.md](adr-independence.md) | ADR: единственный k8s writer |

## Не путать

| Термин | Что это | Где |
|--------|---------|-----|
| **Project Container** | Runtime isolator проекта | этот модуль |
| **Cabinet bundle / starter bundle** | Zip seed кабинета (`cabinet.bundle`) | [05 bundle-format](../05-cabinets/bundle-format.md) |
| **Admin «Бандлы»** | Устаревший chrome каталога starter bundles | deprecate → nav «Контейнеры» + «Кабинеты» stub |

## Rollout (код — только после merge канона)

| Phase | Deliverable |
|-------|-------------|
| **P0** | Этот docs-пакет |
| **P1** | Admin nav: Контейнеры (read-model) + Кабинеты stub; убрать tab Bundles |
| **P2** | ORM `project_containers` + `ContainerRuntimePort` stub (object-ws pause/wipe) |
| **P3** | Real Pod isolator + NetworkPolicy + force-kill + metrics |
| **P4** | Rich admin detail (grouped meta from related entities + k8s) |
