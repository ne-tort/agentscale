# Plugin-модули: целевая архитектура

**Статус:** целевая модель (следующий этап после declarative-app).

## Идея

Модуль — это **полноценное приложение**, которое:
- Загружается **динамически** (из БД, реестра артефактов, или отдельного контейнера)
- Встраивается как **iframe** (или аналогичная изоляция: Web Worker + MessageChannel, isolated origin)
- Получает **ограниченный, но реальный доступ** к инфраструктуре через **capability adapters** с ACL и квотами
- Работает как **отдельная страница/сервис** со своей логикой, состоянием, и (опционально) своей БД/Redis
- ACL/квоты/изоляция — на уровне адаптеров, а не "всё или ничего"

Это **capability-based plugin architecture**, а не "декларативный CRUD-конструктор".

## Почему declarative-app недостаточен

Текущая модель (meta + generic runtime) решает:
- Перенос данных из старых модулей (`mod_prompts`/`mod_mcp`/`mod_files` → `mod_management`)
- Единую точку материализации в workspace
- Единую систему прав (bind_kind + child_may_edit + SoT)

Но она **не позволяет**:
- Модулю иметь свою сложную бизнес-логику (подбор оборудования, тендеры, бюджетирование — это 11–12 тыс. строк кода, а не 50 JSON-описаний экранов)
- Модулю хранить данные в своей схеме/БД (не только `module_instance_data_rows`)
- Модулю иметь свой runtime (отдельный контейнер, свой стек, свои зависимости)
- Динамически загружать код модуля без пересборки основного приложения

## Целевая модель

### 1. Plugin как изолированное приложение

```
Основное приложение (shell)
    ├── iframe / isolated origin
    │       └── Plugin App (отдельный bundle / контейнер)
    │               ├── Своя БД (опционально, через adapter)
    │               ├── Свой Redis (опционально, через adapter)
    │               ├── Свои объекты (MinIO bucket с префиксом)
    │               └── Свои Pod'ы / инструменты (через gateway)
    └── Capability Adapters (ACL + quotas + audit)
            ├── DB Adapter (tenant-aware connection, RLS, quotas)
            ├── Redis Adapter (key prefix, rate limit)
            ├── Object Storage Adapter (bucket prefix, signed URLs)
            └── Pod Gateway (scoped access to project workspace)
```

### 2. Capability Adapters

Адаптеры — это **единственная точка доступа** плагина к инфраструктуре:

| Адаптер | Что даёт | ACL | Квоты | Аудит |
|---------|----------|-----|-------|-------|
| `db` | Query/upsert rows в tenant-aware схеме | `module:{id}:db:{table}:read/write` | `max_rows_per_table`, `max_query_time` | `plugin_db_access` |
| `redis` | Pub/sub, cache, locks | `module:{id}:redis:{key_prefix}` | `max_keys`, `max_memory` | `plugin_redis_access` |
| `objects` | Upload/download с signed URLs | `module:{id}:objects:{bucket_prefix}` | `max_object_size`, `max_objects` | `plugin_object_access` |
| `pods` | Запуск инструментов в Pod'е проекта | `module:{id}:pods:{project_id}` | `max_pod_cpu`, `max_pod_memory` | `plugin_pod_invoke` |
| `mcp` | Регистрация MCP tools | `module:{id}:mcp:{tool}` | `max_tools` | `plugin_mcp_register` |

Адаптеры реализуют **capability-based access control**: плагин получает capability-токен при инициализации, и каждое обращение проверяется.

### 3. Динамическая загрузка

Плагин загружается **не на этапе сборки**, а:
- При grant/bind модуля к кабинету/проекту
- Из реестра артефактов (artifact digest + signature)
- Опционально: из отдельного контейнера (Pod с plugin-app)

Shell рендерит `<iframe src="plugin-origin" sandbox="allow-scripts allow-same-origin">` (или Web Worker + MessageChannel для более жёсткой изоляции).

### 4. Lifecycle

```
Plugin Definition (registry)
    → Grant (company/cabinet/project)
        → Install (развёртывание артефакта, миграция схемы)
            → Bind (связь с project workspace)
                → Runtime (iframe + adapters)
                    → Uninstall (очистка данных, отзыв capabilities)
```

## Отличия от текущей модели

| Аспект | declarative-app (текущая) | plugin-app (целевая) |
|--------|---------------------------|----------------------|
| UI | JSON-описание экранов (tables, views, tabs) | Полноценный React/Vue/Svelte bundle |
| Данные | Только `module_instance_data_rows` | Своя БД (через adapter) |
| Логика | Generic CRUD + materialize | Произвольный код |
| Изоляция | Нет (shared runtime) | iframe / isolated origin |
| Доступ к инфраструктуре | Через generic endpoints | Через capability adapters с ACL |
| Динамическая загрузка | Нет (build-time registry) | Да (реестр артефактов) |
| Сложность | Низкая | Высокая (sandbox, adapters, audit) |

## Путь миграции

### Этап 1 (текущий): declarative-app как основной путь

- `mod_management`, `mod_equipment` — declarative
- `declarative-app` registry — build-time
- Capability gateway — не экспонирован

### Этап 2: capability adapters (подготовка)

- Реализовать `db`, `redis`, `objects`, `pods` adapters с ACL/квотами
- Аудит всех обращений (`plugin_*_access` events)
- Tenant-aware connection pooling

### Этап 3: plugin runtime (пилот)

- `code-plugin` режим в `plugin-host.ts` (уже есть заготовка)
- Isolated origin + postMessage RPC
- Capability token issuance при bind

### Этап 4: dynamic loading (цель)

- Реестр артефактов (artifact digest + signature)
- Динамическая загрузка iframe src
- Lifecycle: install/uninstall/migrate

### Этап 5: deprecate declarative-app (опционально)

- Только для простых CRUD-модулей (типа `mod_management`)
- Сложные модули (`mod_equipment`) мигрируют в plugin

## Риски и ограничения

1. **Безопасность** — sandbox (CSP, iframe sandbox, opaque origin) не даёт 100% изоляции; нужен WASM sandbox / Firejail / k8s namespace для production.
2. **Сложность** — capability adapters требуют tenant-aware инфраструктуры (RLS, key prefixes, bucket prefixes).
3. **Миграция данных** — declarative-app хранит данные в `module_instance_data_rows`; plugin-app может иметь свою схему — нужен migration path.
4. **Операционная сложность** — отдельные контейнеры модулей = управление жизненным циклом, версионирование схем, миграции.

## Связанные документы

- `docs/migration-architecture.md` — текущая миграционная архитектура (declarative-app)
- `docs/architecture-migration/ADR-001-dynamic-ui-sandbox.md` — решение о build-time регистрации
- `docs/08-migration/dynamic-ui-implementation-contract.md` — контракт declarative-app
- `docs/target/05-cabinets/mcp-packages.md` — MCP packages как предшественник plugin (zip + deploy)

## Статус

**Целевая модель.** Реализация — отдельная миграция (не входит в текущую declarative-app волну).
