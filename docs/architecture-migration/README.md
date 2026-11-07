# Реестр ADR

| Номер | Решение | Дата | Статус |
|---|---|---|---|
| 001 | Dynamic UI sandbox: build-time registry, код из БД не исполняется | 2026-11-01 | Superseded where applicable ADR-006 |
| 002 | Capability gateway: единая прикладная точка доступа, typed contract | 2026-11-01 | Действует |
| 003 | No direct infra access: Pod/агент не получают прямых credentials | 2026-11-01 | Действует |
| 004 | Runtime revision: immutable `ProjectRuntimeRevision`, `applied_revision_id` | 2026-11-01 | Действует |
| 005 | Equipment bounded context: источник цены, свежесть, provenance | 2026-11-01 | Действует |
| 006 | Plugin-модули как целевая модель | 2026-11-07 (пересмотрено 2026-11-08) | Принято |

## Правила

- Номер присваивается следующим по порядку, не переиспользуется.
- Новое решение, отменяющее старое, явно помечает `Supersedes` / `Superseded where applicable`; старый файл не удаляется.
- Источник истины по дизайну плагинов: `docs/ARCHITECTURE/plugin-architecture-complete.md`.
- При конфликте ADR между собой приоритет у более позднего при явной пометке; иначе конфликт считается незакрытым и реализация не начинается.

## Известные конфликты

- Прямой `TENANT_DB_DSN` плагину (enterprise-режим) конфликтует с ADR-003. До выбора этого варианта требуется отдельный ADR с явным supersede. v2 дизайна по умолчанию использует gateway без DSN.
- ADR-001 запрещает динамическую загрузку кода; ADR-006 разрешает её для plugin-app. Помечено как Superseded where applicable.
