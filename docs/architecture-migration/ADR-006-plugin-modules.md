# ADR-006: Plugin-модули как целевая модель

**Статус:** Принято (2026-11-07), пересмотрено 2026-11-08 после архитектурного ревью.  
**Номер:** 006 (первоначально ошибочно опубликован как ADR-002; ADR-002 уже занят `capability-gateway`).  
**Связанные:** ADR-001 (build-time UI), ADR-002 (capability gateway), ADR-003 (no direct infra access), ADR-004 (runtime revision), ADR-005 (equipment bounded context).

## Контекст

Текущая модель модулей (declarative-app) — JSON-описание экранов + generic runtime — решила задачу переноса данных из старых модулей (`mod_prompts`/`mod_mcp`/`mod_files` → `mod_management`) и даёт единую систему прав (bind_kind + child_may_edit + SoT).

Но она не позволяет:
- модулю иметь сложную бизнес-логику (`mod_equipment` — ~13k строк: 18 Python-файлов / 9 678 строк + 16 TS/TSX / 3 485 строк);
- хранить данные в **реальных таблицах** (сейчас всё в `module_instance_data_rows` JSONB);
- иметь свой runtime и динамически загружать код без пересборки shell.

Ранняя документация описывала модуль как версионируемый пакет кода, но ADR-001 сознательно ограничил это build-time регистрацией declarative-app.

## Решение

**Целевая модель** — plugin-модули: полноценные приложения (frontend + backend), загружаемые динамически, с доступом к инфраструктуре через **существующий capability gateway** (`CapabilityGatewayService`, pod-gateway `:8001`, `PodBridgeClaims`).

### Ключевые принципы

1. **Переиспользование as-built** — не строить новый сервис адаптеров, не mint'ить новый формат JWT, не создавать БД на компанию. Расширяем `CapabilityGatewayService`, `TenantUserDbService`, `tenant_infra/*`, `:8001`. См. полный дизайн: `docs/ARCHITECTURE/plugin-architecture-complete.md` (v2).
2. **Реальные таблицы без прямой выдачи DSN** — структурированный манифест схемы, DDL исполняет доверенная платформенная роль; плагин получает typed `db.query`/`db.mutate` через gateway. Прямой DSN возможен только в отдельном enterprise-режиме и требует отдельного ADR, отменяющего ADR-003.
3. **Миграции как инфраструктура** — плагин поставляет Liquibase/Flyway/Alembic changelog; платформа исполняет его в изолированном Job под доверенной ролью, с валидацией запрещённых statement, подписью артефакта и `DATABASECHANGELOG`. Произвольный SQL из тела HTTP-запроса запрещён (ADR-002).
4. **Изоляция** — отдельная схема `tenant_<company>_module_<module>`, grant без `CREATE`, отдельный origin для iframe **без** `allow-same-origin`, NetworkPolicy без egress к PostgreSQL. RLS не является границей.
5. **Единый фаерволл** — egress-контроллер с режимами `none`/`webhooks-only`/`full` для плагинов **и** контейнеров агентов.
6. **Засыпание** — переиспользование `SandboxClaim.operatingMode` / `PodRuntimePort.pause|resume` / warm pool; Knative не вводится (нет CRD, нет whitelist в Argo AppProject).
7. **Внешние адреса** — path-based `/webhook/{plugin}/{company}/*` и `/pluginpage/{plugin}/{company}/*` на отдельном Ingress с `router.priority: 120`.

### Конечное состояние

Declarative-app удалён **полностью** («всё под нож»): `module_meta_documents`, generic runtime, `module_instance_data_rows`, `ModuleRuntime.tsx`.

### Метод перехода

Не big bang. Strangler с фича-флагом на grant/company: слой capability → пилот за флагом → dual-read → перенос frontend → перенос backend по одному bounded context'у (ADR-005) → cutover → удаление. Причины: ~13k строк, работа прямо в `main` с auto-merge, единственный dev-контур, отсутствие бэкапов (`topology.md:208-220`), DOWN-миграции не поддерживаются.

## Последствия

### Положительные

- Реальные таблицы, типы, индексы, constraints вместо JSONB.
- Сложная бизнес-логика живёт в плагине, а не раздувает generic runtime.
- Один ACL/квота/аудит/отзыв токена (существующий gateway), а не две реализации.
- Динамическая загрузка кода, внешние webhook'ы и страницы, общее с агентами засыпание.

### Риски и митигации

| Риск | Митигация |
|---|---|
| Произвольный DDL затирает чужие схемы | changelog → доверенная роль → изолированный Job, статическая валидация, `NOCREATEDB NOCREATEROLE` |
| Межтенантовая утечка | отдельная схема + grant без `CREATE` + gateway проверяет `company_id` из JWT |
| Подмена `X-Plugin-*` | Gateway перезаписывает заголовки |
| iframe снимает sandbox | отдельный origin, CSP, `postMessage` с проверкой origin, без `allow-same-origin` |
| Неоткатываемый cutover | strangler + dual-read + snapshot до переноса |
| `/webhook/` уезжает в SPA | `router.priority: 120` + тест приоритетов |

## Альтернативы

1. **Расширить declarative-app** — отклонено: не даёт реальных таблиц и своего runtime.
2. **WASM plugins** — отложено: требует WASM-компиляции всех зависимостей.
3. **Отдельный PG-инстанс / БД на компанию с прямым DSN** — отложено в enterprise-tier: нет `max_size`, нет PgBouncer, нет бэкапов, single-node k3s, конфликт с ADR-003.
4. **Knative Serving** — отклонено: нет CRD и whitelist в AppProject; pause/resume уже реализован.
5. **Wildcard-поддомены `{plugin}.appname.com`** — отклонено: нужен wildcard-сертификат и интеграция с DNS-провайдером; в кластере self-signed TLSStore.

## Связанные документы

- `docs/ARCHITECTURE/plugin-architecture-complete.md` — **полный дизайн v2 (источник истины)**
- `docs/ARCHITECTURE/plugin-modules.md` — краткое введение
- `docs/PRODUCT.md` — раздел «Целевая модель: plugin-модули»
- `docs/workflow/main-branch-workflow.md` — работа на main

## Статусы соседних ADR

- ADR-001 (build-time UI registry) — **Superseded where applicable** настоящим ADR.
- ADR-002 (capability gateway) — **действует**, расширяется, не отменяется.
- ADR-003 (no direct infra access) — **действует**; вариант с прямым DSN требует его явного supersede.
- ADR-004 / ADR-005 — действуют; определяют параллельные ревизии и границы equipment-контекста.
