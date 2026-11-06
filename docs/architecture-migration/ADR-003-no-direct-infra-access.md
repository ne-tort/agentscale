# ADR-003: No direct infrastructure access

- **Статус:** Принято
- **Дата:** 2026-11-01

## Контекст

Agent runtime живёт в изолированном Pod и должен работать с workspace и разрешёнными capability. Прямой доступ к Postgres, MinIO, Kubernetes API, секретам, Commerce/S4B DB или shared filesystem обходит tenancy, аудит и lifecycle-контроль.

## Решение

Pod и агент не получают прямых credentials или сетевого доступа к инфраструктурным хранилищам и control plane. Все операции идут через scoped platform API/capability gateway и short-lived credential leases. Секреты представлены references, а не plaintext content; bridge credentials ротируются и отзываются независимо от обычного env.

Инфраструктурные адаптеры, ingestion и materialization выполняются платформенными сервисами. Pod получает только materialized workspace, разрешённые MCP/tools и минимальный bridge scope.

## Последствия

- Утечка или компрометация агента не даёт прямого доступа к tenant storage или кластеру.
- Все внешние операции можно связать с project/session/run и policy decision.
- Платформе нужны явные API для файлов, событий, документов и доменных действий.
- Любой новый интеграционный путь должен проходить gateway, а не добавлять mount, DB credential или Kubernetes permission в Pod.
