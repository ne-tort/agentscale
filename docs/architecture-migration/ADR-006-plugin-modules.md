# ADR-006: Plugin-модули как целевая модель

**Статус:** Принято (2026-11-07), пересмотрено 2026-11-08 и 2026-11-09 после архитектурного ревью. **Это target design, а не as-built:** на audited HEAD `6ee720dd` plugin v4 не является подтверждённым vertical slice и остаётся emergency disabled.
**ADR-009:** авторинг/self-service отдельно предложен ADR-009 и пока не принят.
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

**Целевая модель** — plugin-модули: полноценные приложения (frontend + backend), загружаемые динамически, с переиспользованием кода `CapabilityGatewayService` в **отдельном trust-контуре `PluginClaims` + `/capabilities/v1/*` в Plugin Gateway**. Agent pod-gateway `:8001` и `PodBridgeClaims` не расширяются и не используются плагинами.

### Ключевые принципы

1. **Переиспользование кода, а не транспорта/identity** — ядро `CapabilityGatewayService`, audit/quota/grant contracts и domain registry переиспользуются. Для плагинов создаются `PluginClaims` и `/capabilities/v1/*` в Plugin Gateway, отдельная tenant БД на компанию и scoped objects; `TenantUserDbService` остаётся для агентов. См. v4-дизайн только вместе с errata и ADR-008.
2. **Реальные таблицы и прямой DSN** — отдельная БД `tenant_<company_id>`, схема `module_<module_id>` во владении роли плагина, полный SQL и self-managed Liquibase/Flyway/Alembic миграции. Плагин **не** получает доступ к платформенной `prodavan`. Подробности и границы — ADR-007.
3. **Миграции как инфраструктура** — плагин поставляет подписанный Liquibase/Flyway/Alembic changelog и применяет его сам при старте контейнера под своей ролью (`NOCREATEDB NOCREATEROLE`). Безопасность = привилегии PostgreSQL + отдельная БД + `pgaudit`, а не парсер SQL. Произвольный SQL из тела HTTP-запроса к платформе отсутствует (ADR-002).
4. **Изоляция** — отдельная **БД** `tenant_<company>`, `GRANT CONNECT` только на неё, схема с owner = плагин, NetworkPolicy без egress к `prodavan`, отдельный origin для iframe **без** `allow-same-origin`. RLS не является границей.
5. **Единая схема фаерволла, не единая реализация** — правила `none`/`webhooks-only`/`full` для плагинов **и** агентов из общего генератора и с общим audit envelope; implementation не объединяет agent и plugin lifecycle.
6. **Засыпание** — Deployment `replicas: 0` + Plugin Gateway activator (ADR-008). `SandboxClaim` остаётся только агентам; Knative не вводится.
7. **Внешние адреса** — path-based `/webhook/{plugin}/{company}/*` и `/pluginpage/{plugin}/{company}/*` на отдельном Ingress с `router.priority: 120`.

### Конечное состояние (target)

Declarative-app удаляется **только после подтверждённого code-first strangler cutover**: `module_meta_documents`, generic runtime, `module_instance_data_rows`, `ModuleRuntime.tsx`. Это не разрешение текущего cutover и не основание удалять migration-only helpers.

### Метод перехода

Strangler с фича-флагом — **только из-за объёма кода** `mod_equipment` (~13k строк), а не из-за данных. Это dev-разработка: данные эфемерны, **бэкапы, перенос данных, snapshot данных, dual-read/dual-write, `migrate-to-plugin` и длительное наблюдение не требуются**. Исходный snapshot/patch агента — отдельный, предложенный ADR-009 механизм authoring.

## Последствия

### Положительные

- Реальные таблицы, типы, индексы, constraints вместо JSONB.
- Сложная бизнес-логика живёт в плагине, а не раздувает generic runtime.
- Общее ядро ACL/квота/аудит/grant и отдельный `PluginClaims`, а не повторное использование `PodBridgeClaims`.
- Динамическая загрузка кода, внешние webhook'ы и страницы, общий lifecycle-контракт при разных реализациях для агентов и плагинов.

### Риски и митигации

| Риск | Митигация |
|---|---|
| Произвольный DDL затирает чужие БД | отдельная БД + `GRANT CONNECT` только на неё + `NOCREATEDB NOCREATEROLE` + NetworkPolicy без `prodavan` + `pgaudit` (ADR-007) |
| Межтенантовая утечка | исключена конструктивно: роль не имеет `CONNECT` к чужой БД |
| Подмена `X-Plugin-*` | Gateway строит headers allowlist'ом |
| iframe снимает sandbox | отдельный origin, CSP, `postMessage` с проверкой origin, без `allow-same-origin` |
| Сломанный cutover | Текущий статус STOP: cutover запрещён до live acceptance. Target — code-first expand/contract A→B; rollback кода не означает destructive rollback БД. См. `docs/migration/plugin-cutover-safety.md` |
| `/webhook/` уезжает в SPA | `router.priority: 120` + тест приоритетов |

## Альтернативы

1. **Расширить declarative-app** — отклонено: не даёт реальных таблиц и своего runtime.
2. **WASM plugins** — отложено: требует WASM-компиляции всех зависимостей.
3. **Typed gateway без DSN (вариант A)** — отклонено: `_ALLOWED_TYPES` / структурированный манифест запрещает произвольный SQL, JOIN, CTE, индексы и миграции, то есть воспроизводит ту же ограниченную декларативность. Выбран вариант B (ADR-007).
4. **Knative Serving** — отклонено: нет CRD и whitelist в AppProject; pause/resume уже реализован.
5. **Wildcard-поддомены `{plugin}.appname.com`** — отклонено: нужен wildcard-сертификат и интеграция с DNS-провайдером; в кластере self-signed TLSStore.

## Связанные документы

- `docs/ARCHITECTURE/plugin-architecture-complete.md` — **v4 target snapshot, только вместе с authoritative errata**
- `docs/architecture-migration/ADR-007-direct-plugin-db.md` — прямой DSN
- `docs/architecture-migration/ADR-008-lifecycle-and-boundaries.md` — разделение trust/lifecycle, замена as-built привязок
- `docs/ARCHITECTURE/plugin-modules.md` — краткое введение
- `docs/PRODUCT.md` — раздел «Целевая модель: plugin v4 (TARGET, не as-built)»
- `docs/PLUGIN-AUTHORING.md` — Supported/Target/Not implemented матрица
- `docs/migration/plugin-cutover-safety.md` — безопасный cutover и STOP gates
- `AGENTS.md` и `docs/LEGACY.md` — действующий workflow и категории документации

## Статусы соседних ADR

- ADR-001 (build-time UI registry) — **Superseded where applicable** настоящим ADR; запрет исполняемого неподписанного кода остаётся.
- ADR-009 (source authoring/self-service) — **Предложено**; не as-built и не supersede ADR-006 до принятия.
- ADR-002 (capability gateway) — **действует**, расширяется, не отменяется.
- ADR-003 (no direct infra access) — **действует**; вариант с прямым DSN требует его явного supersede.
- ADR-004 / ADR-005 — действуют; определяют параллельные ревизии и границы equipment-контекста.
