# 01 — Метасинтаксис и модули

## Контекст

Метасинтаксис — декларативный способ описать UI модуля (tables/columns/views/tabs), данные (seed/rows), actions и **что материализуется в workspace Pod** (файлы, промпты, MCP, env). Модули привязываются к кабинету/проекту (`bind_kind`: local | global) и каскадируют инстансы Platform → Company → Cabinet → Project.

Продуктовый intent: широкая кастомизация контейнера через UI + агент пишет данные, видимые в UI. Legacy-канон: [`docs/target/06-modules/meta-syntax/`](../../target/06-modules/meta-syntax/). Ориентир продукта: [`docs/PRODUCT.md`](../../PRODUCT.md).

## Текущая реализация (as-is)

### Materialize pipeline

Три слоя:

1. **Planner** — [`materialize_planner.py`](../../../apps/api/src/prodavan/application/projects/materialize_planner.py)  
   Резолвит `MaterializeRule[]` из meta-slug `materialize`, мержит auto-правила из колонок `file_ref` (`_auto_rules_from_columns`), фильтрует строки (`_row_applies_to_project`, `_row_matches_filter`), подставляет плейсхолдеры, сшивает `prompt_fragment` → один `raw` на path (`_stitch_prompt_fragment_ops`).

2. **Orchestrator** — [`materialize.py`](../../../apps/api/src/prodavan/application/projects/materialize.py)  
   `materialize_project` / `sync_project` (с prune disabled модулей). Дополнительно пишет `AGENTS.md`, `mcp.json`, `openclaw-config.yaml`. Platform MCP пакеты подключаются **прямым импортом** (`materialize_platform_modules_mcp`, `materialize_platform_equipment_mcp`).

3. **Executor** — [`materialize_executor.py`](../../../apps/api/src/prodavan/application/projects/materialize_executor.py)  
   Форматы: `raw`, `copy_blob`, `mcp_package`, `json_rows` / `json_single`, `template`, `merge_mapped_sqlite`, `prompt_paths` → fragments.

Source types: `row` / `rows` / `file_ref` / `meta_document` / `mcp_package` / `static`.

### Модули и SoT

| Сервис | Файл | Роль |
|--------|------|------|
| Binding | [`module_binding_service.py`](../../../apps/api/src/prodavan/application/modules/module_binding_service.py) | N:M Module↔Cabinet/Project/Company; `bind_kind` local/global |
| Instances | [`module_instance_service.py`](../../../apps/api/src/prodavan/application/modules/module_instance_service.py) | Cascade fork; `resolve_sot_instance` walk-up; `sot_may_edit` |
| Meta docs | [`module_meta_service.py`](../../../apps/api/src/prodavan/application/modules/module_meta_service.py) | CRUD JSONB slug-документов |
| Actions | [`module_action_executor.py`](../../../apps/api/src/prodavan/application/modules/module_action_executor.py) | Декларативные actions + seed fallback |
| Seeds | [`product_module_seeds.py`](../../../apps/api/src/prodavan/application/platform/product_module_seeds.py) | Python-каталог продуктовых модулей |
| ACL | [`project_service/access.py`](../../../apps/api/src/prodavan/application/project_service/access.py) | `ProjectAccessPolicy`, visibility |

Иерархия владельцев: `OWNER_PLATFORM / OWNER_COMPANY / OWNER_CABINET / OWNER_PROJECT`.  
Исключение записи в parent при global: `WRITABLE_GLOBAL_PROJECT_MODULES = {mod_equipment}` в [`domain/modules/types.py`](../../../apps/api/src/prodavan/domain/modules/types.py).

### Row / project фильтр

`_row_applies_to_project`: пустой / отсутствующий `project_ids` трактуется как **все проекты**, к которым модуль bound. Это мягкий фильтр, не deny-by-default.

### Межмодульное общение

Модули **не** публикуют/подписываются на Kafka-топики друг друга. Связь:

- shared SoT через `resolve_sot_instance`;
- прямой импорт сервисов внутри planner/executor;
- Kafka несёт platform/auth/relation/metrics (опционально), не module-to-module.

См. также [05-cross-cutting](05-cross-cutting.md).

## Проблемы

### META-P0a — soft isolation через `project_ids`

**Приоритет:** P0  
Строка без явного `project_ids` материализуется во все bound проекты кабинета. Оператор должен помнить «явно исключать», иначе утечка конфигурации/данных между проектами.

**Где:** `_row_applies_to_project` в planner (+ зеркала в instance filters).

### META-P0b — writable global SoT

**Приоритет:** P0  
`WRITABLE_GLOBAL_PROJECT_MODULES={mod_equipment}` позволяет агенту писать chat-scoped строки в parent SoT при global bind. При параллельных сессиях разных проектов — гонки и смешение данных.

### META-P1a — «микросервисы через Kafka» не реализованы

**Приоритет:** P1  
Модули связаны синхронно in-proc. `KafkaManager` в buffer-only режиме делает `_local_auth_dispatch` / `_local_metrics_dispatch`. PG outbox остаётся SoT до cutover (documented hole).

**Где:** [`kafka_manager.py`](../../../apps/api/src/prodavan/core/infra/kafka_manager.py), [`core/events/bus.py`](../../../apps/api/src/prodavan/core/events/bus.py).

### META-P1b — два источника истины для actions

**Приоритет:** P1  
DB meta slug `actions` + `_product_seed_action` из Python seeds, когда DB отстаёт. Два контракта UI/агента.

**Где:** [`module_action_executor.py`](../../../apps/api/src/prodavan/application/modules/module_action_executor.py), [`product_module_seeds.py`](../../../apps/api/src/prodavan/application/platform/product_module_seeds.py).

### META-P1c — хрупкие плейсхолдеры

**Приоритет:** P1  
~~`_substitute` поддерживает и `{{var}}` (Mustache), и `{var}` (regex `[a-z_]+`). Порядок важен: `{target_path}` внутри `{{target_path}}` ломается.~~  
**Исправлено:** единый `application/projects/template_substitute.py` — только `{{var}}` Mustache dialect (`substitute` + `substitute_blanking_missing` для file content). Planner и executor делегируют; отдельный regex в `_render_template` удалён. Seeds мигрированы: `{active_profile_id}` → `{{active_profile_id}}`. Валидатор (`module_meta_validator._validate_template_dialect`) отклоняет single-brace `{var}` в `workspace_path` / `template` / `source.filter.*` с diagnostic. Тесты обновлены под новый dialect.

### META-P2a — хардкод `mod_prompts`

**Приоритет:** P2  
`active_profile_id` резолвится только для prompts в planner — не декларативный selection.

### META-P2b — legacy merge и опасный prune

**Приоритет:** P2  
`merge_mapped_sqlite` — legacy merge артефактов. `sync_project` делает `wipe_prefix` по `workspace_roots`; кривой meta-slug может стереть чужие пути в workspace.

## Target-design

### Контракты

1. **MaterializeRule → MaterializeOp** — единый декларативный контракт без хардкода имён модулей. Selection профилей — общий механизм (`selection` block в meta), не case в planner.
2. **Один template dialect** — только Mustache `{{var}}` (или только один regex); миграция существующих rules.
3. **Изоляция deny-by-default:**
   - `project_ids` empty / missing → **только текущий project** (или «нет проектов» — выбрать одну семантику и зафиксировать в PRODUCT);
   - opt-in `scope: all_bound` для явного шаринга.
4. **Global bind = read-only по умолчанию.** Запись в parent — только через явный `writable_global` + per-row / per-session lock или outbox merge.
5. **Event-driven modules:** публикация `module.data.changed` / `module.meta.changed` в Kafka; materialize и зависящие модули — подписчики. Прямые импорты сервисов между BC запрещены (кроме shared domain ports).
6. **Actions SoT = DB meta.** Seeds — только bootstrap пустой БД / first install, затем зеркало в meta и удаление runtime-fallback.

### Ответственность

| Компонент | Владеет | Не владеет |
|-----------|---------|------------|
| Meta documents | Схема UI + rules | Runtime write в Pod |
| Planner | Резолв ops с ACL | Запись файлов |
| Executor | Идемпотентная запись workspace | Выбор SoT / ACL |
| Binding service | N:M graph bind_kind | Materialize contents |
| Instance service | Cascade fork / SoT resolve | Kafka publish (через port) |

### UX / семантика

- В UI явно показывать scope строки: «этот проект» / «все bound» / список id.
- При global bind — баннер «данные общие; запись ограничена».
- Conflict двух rules на один `workspace_path` — ошибка валидации meta, не last-write / silent stitch без маркеров (stitch для prompts — осознанный merge по priority, задокументированный в PRODUCT).

## Шаги рефакторинга

1. Зафиксировать в [`PRODUCT.md`](../../PRODUCT.md) семантику `project_ids` (deny-by-default) и global writable policy.
2. Изменить `_row_applies_to_project` + тесты; миграция существующих строк с пустым `project_ids` → явный `scope` или список id.
3. Убрать / сузить `WRITABLE_GLOBAL_PROJECT_MODULES`; для equipment — явный merge-сервис или local fork по умолчанию для записей агента.
4. Вынести `active_profile` в декларативный selection; убрать хардкод `mod_prompts` из planner.
5. Унифицировать `_substitute` на один dialect; валидатор meta на CI (`alembic check`-подобно для meta JSON).
6. Удалить runtime seed-fallback actions после гарантии sync meta из seeds при install.
7. Ввести topic `prodavan.module.events` + publisher из instance/meta services; materialize scheduler — consumer (см. [05](05-cross-cutting.md)).
8. Platform MCP materialize — как подписка/правило модуля platform, не hardcode в [`materialize.py`](../../../apps/api/src/prodavan/application/projects/materialize.py).

## Ссылки

- As-built слои модулей: [`docs/target/12-layer-docs/`](../../target/12-layer-docs/)
- Legacy meta: [`docs/target/06-modules/meta-syntax/`](../../target/06-modules/meta-syntax/)
- Prompt stitch (уже в коде): [`prompt_stitch.py`](../../../apps/api/src/prodavan/application/projects/prompt_stitch.py)
