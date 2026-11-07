# ADR-002: Plugin-модули как целевая модель

**Статус:** Принято (2026-11-07)

## Контекст

Текущая модель модулей (declarative-app) — JSON-описание экранов + generic runtime — решает задачу переноса данных из старых модулей (`mod_prompts`/`mod_mcp`/`mod_files` → `mod_management`) и предоставляет единую систему прав (bind_kind + child_may_edit + SoT).

Однако она **не позволяет**:
- Модулю иметь сложную бизнес-логику (подбор оборудования, тендеры, бюджетирование — это 11–12 тыс. строк кода, а не 50 JSON-описаний)
- Модулю хранить данные в своей схеме/БД (не только `module_instance_data_rows`)
- Модулю иметь свой runtime (отдельный контейнер, свой стек, свои зависимости)
- Динамически загружать код модуля без пересборки основного приложения

Ранняя документация (`docs/02-architecture/platform-extensibility.md`) описывала модуль как версионируемый пакет кода на отдельном origin, но миграционная архитектура (`docs/migration-architecture.md`, ADR-001) сознательно ограничила это до build-time регистрации declarative-app.

## Решение

**Целевая модель** — plugin-модули: полноценные приложения, загружаемые динамически (iframe / isolated origin), с доступом к инфраструктуре через **capability adapters** (ACL + квоты + аудит).

### Ключевые принципы

1. **Capability-based access** — плагин получает capability-токен при инициализации; каждое обращение к инфраструктуре проверяется адаптером (tenant-aware connection, key prefixes, bucket prefixes, rate limiting, audit).

2. **Изоляция** — плагин исполняется в iframe / isolated origin / (будущем) WASM sandbox; не имеет прямого доступа к shared runtime.

3. **Динамическая загрузка** — плагин загружается при grant/bind, а не на этапе сборки; реестр артефактов (artifact digest + signature).

4. **Адаптеры как единственная точка доступа** — DB, Redis, Objects, Pods, MCP — все через адаптеры с ACL/квотами.

### Отличия от declarative-app

| Аспект | declarative-app | plugin-app |
|--------|-----------------|------------|
| UI | JSON-описание экранов | Полноценный React/Vue/Svelte bundle |
| Данные | Только `module_instance_data_rows` | Своя БД (через adapter) |
| Логика | Generic CRUD + materialize | Произвольный код |
| Изоляция | Нет (shared runtime) | iframe / isolated origin |
| Доступ к инфраструктуре | Через generic endpoints | Через capability adapters с ACL |
| Динамическая загрузка | Нет (build-time registry) | Да (реестр артефактов) |

## Последствия

### Положительные

- Модуль может иметь сложную бизнес-логику (подбор оборудования, тендеры, бюджетирование) без раздувания generic runtime.
- Модуль может хранить данные в своей схеме/БД (tenant-aware через adapter).
- Модуль может иметь свой runtime (отдельный контейнер, свой стек).
- Динамическая загрузка кода без пересборки основного приложения.
- Capability-based ACL позволяет точно контролировать, к чему модуль имеет доступ.

### Отрицательные / риски

- **Безопасность** — sandbox (CSP, iframe sandbox, opaque origin) не даёт 100% изоляции; нужен WASM sandbox / Firejail / k8s namespace для production.
- **Сложность** — capability adapters требуют tenant-aware инфраструктуры (RLS, key prefixes, bucket prefixes, rate limiting, audit).
- **Миграция данных** — declarative-app хранит данные в `module_instance_data_rows`; plugin-app может иметь свою схему — нужен migration path.
- **Операционная сложность** — отдельные контейнеры модулей = управление жизненным циклом, версионирование схем, миграции.
- **Тестирование** — интеграционные тесты должны покрывать adapter ACL/квоты/аудит.

### Нейтральные

- `mod_management` (простой CRUD + materialize) может остаться declarative-app.
- `mod_equipment` (сложная логика подбора, 11–12 тыс. строк кода) мигрирует в plugin-app.
- Capability gateway (`CapabilityGatewayService`) переиспользуется из dynamic-apps подсистемы.

## Альтернативы

### Альтернатива 1: Расширить declarative-app

Добавить больше виджетов, форматов, action kinds.  
**Отклонено:** не решает проблему "модуль не может иметь свою БД/Redis/runtime"; declarative-app по определению ограничен generic runtime.

### Альтернатива 2: WASM plugins

Плагин компилируется в WASM, исполняется в sandbox'е основного процесса.  
**Отклонено (пока):** WASM sandbox сложнее в реализации, чем iframe + adapters; требует WASM-компиляции всех зависимостей (БД-драйверы, Redis client и т.д.). Оставлено как future work.

### Альтернатива 3: Отдельные сервисы (sidecar)

Каждый модуль — отдельный сервис (Pod), общается с основным приложением через gRPC/HTTP.  
**Принято частично:** capability adapters могут быть реализованы как sidecar, но iframe-изоляция предпочтительнее для frontend.

## Связанные документы

- `docs/migration-architecture.md` — текущая миграционная архитектура (declarative-app)
- `docs/architecture-migration/ADR-001-dynamic-ui-sandbox.md` — решение о build-time регистрации (отменяется для plugin-app)
- `docs/architecture/plugin-modules.md` — детальное описание архитектуры
- `docs/PRODUCT.md` — раздел "Целевая модель: plugin-модули"

## Примечание о работе на main

Эта миграция (plugin-modules) будет вестись **сразу на dev ветке (main)**, без отдельных feature-веток. PR будут создаваться напрямую в main, CI Gate → Auto-merge → Verify Dev.

Это отличается от предыдущей миграции (declarative-app), которая велась на feature-ветках (`feat/admin-keys-modules`, `fix/management-merge-scope` и т.д.).
