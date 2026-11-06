# Backend-модульная система и ORM: migration audit

Дата аудита: 2026-11-01

Объект: `apps/api` — SQLAlchemy persistence, module services, meta/DSL interpreters, HTTP/API routes и Alembic. Код не изменялся.

## 1. Executive summary

Система уже имеет рабочий продуктовый контур, но persistence-модель модулей остается гибридной:

1. **Каталог/описание:** `modules` + `module_meta_documents` с JSONB-документами.
2. **Область владения:** отдельные таблицы company grant, cabinet bind, project bind.
3. **Fork/SoT:** `module_instances` и копии `module_instance_meta_documents`.
4. **Данные:** универсальный EAV-подобный слой `module_instance_data_rows(instance_id, table_slug, row_id, body JSONB)`.
5. **Legacy runtime:** динамические схемы кабинетов с `{schema}.module_data_rows`, оставшиеся после прежней модели.
6. **Производные данные:** OpenSearch и workspace-файлы, которые выглядят как таблицы/строки API, но не имеют единой SQL-транзакции с row storage.

Главный вывод: **не нужно превращать весь DSL в набор SQL-таблиц**. Meta/UI/actions/materialize — это декларативная конфигурация и должна остаться версионируемой декларативной моделью. Заменить следует универсальный row-store для стабильных бизнес-сущностей и унифицировать ownership/bind/materialization вокруг typed relational contracts.

Приоритет миграции:

- P0: остановить создание/использование legacy cabinet schema rows и зафиксировать один SoT.
- P1: ввести единый `module_bindings` с typed owner references/constraint strategy и убрать три почти одинаковые binding-таблицы из application API.
- P1: отделить module definition/version/meta от runtime instance и materialization projection.
- P1: вынести `mod_prompts`, `mod_mcp`, `mod_files` из generic JSONB rows в общий content/resource domain.
- P2: вынести equipment и B2B/tenders в доменные relational tables; JSONB оставить только для редких extensibility-полей.
- P2: версионировать DSL и actions, убрать silent seed overwrite и implicit fallback из runtime path.

## 2. Что есть сейчас

### 2.1 ORM-таблицы

В `infrastructure/persistence/models/modules.py` определены:

- `modules`: имя, статус, owner scope/company, `company_grant_scope`.
- `module_meta_documents`: один JSONB body на `(module_id, slug)`.
- `module_company_grants`: grant `(module, company)` + `bind_kind` и `child_may_edit`.
- `module_cabinet_bindings`: bind `(module, cabinet)` + те же флаги.
- `module_project_bindings`: bind `(module, project)` + те же флаги.
- `module_instances`: fork с полиморфным `(owner_kind, owner_id)`, `parent_instance_id`.
- `module_instance_meta_documents`: копия JSONB meta на `(instance, slug)`.
- `module_instance_data_rows`: `(instance, table_slug, row_id)` и произвольный `body JSONB`, плюс `session_id` и `created_by`.

`module_instances.owner_id` — строка без FK, а `owner_kind` — строковый discriminator. Это дает гибкость, но БД не может гарантировать существование owner, допустимую иерархию или соответствие owner module binding.

### 2.2 Реальный жизненный цикл

`ModuleInstanceService` реализует следующую механику:

- platform instance создается лениво;
- local bind делает deep copy meta и rows в child instance;
- global bind не создает child, а `resolve_sot_instance()` поднимается project → cabinet → company → platform;
- project local fork фильтрует rows по `body.project_ids`;
- переключение local/global удаляет project leaf;
- при удалении `mod_equipment` дополнительно удаляются OpenSearch indexes.

Это не обычное наследование, а смесь **snapshot-copy**, **runtime resolution** и **permission policy**. `parent_instance_id` в БД не является источником истины для resolution: resolution фактически вычисляется по bind-таблицам и ownership.

### 2.3 Generic row API

Одинаковые CRUD routes существуют для нескольких owner-контуров:

- `/admin/modules/{module_id}/data/{table_slug}`;
- `/companies/{company_id}/modules/{module_id}/data/{table_slug}`;
- `/cabinets/{cabinet_id}/modules/{module_id}/data/{table_slug}`;
- `/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}`;
- pod-facing `/projects/{project_id}/modules/{module_id}/data/{table_slug}`.

Payload почти везде `{body: dict}`. `table_slug` и `row_id` — application-level identifiers; реальная схема строки описывается в meta `tables`/`columns`. `PATCH` сначала merge-ит JSON, затем валидирует результат.

### 2.4 Meta/DSL

`module_meta_documents.body` содержит JSON-документы `tables`, `columns`, `views`, `tabs`, `actions`, `materialize`, `mcp_tools`, `mcp_aliases`, `container_env`, `container_env_secrets`, `seed_rows`, `materialize_roots`.

`module_meta_validator.py` валидирует только известные структуры и ссылки между slug-ами. `module_row_validator.py` реализует runtime-типы `text/number/bool/datetime/json/enum/ref/file_ref/secret_ref`. `module_action_executor.py` интерпретирует declarative `actions`, но имеет большой hard-coded dispatch по kind и специальную ветвление для `mod_equipment`, документов, remote SQL/OpenSearch.

`module_materialize_service.py` interprets `seed_rows` и создает legacy per-cabinet schema data layer. Workspace materialization использует `materialize` rules и object store.

### 2.5 Prompt/MCP/files

Три базовых модуля описаны тем же DSL:

- `mod_prompts`: `prompt_profiles`, `profile_settings`, `prompt_paths`, причем файлы prompt лежат вложенным `files_json`;
- `mod_files`: rows `files` с `file_ref`, `target_path`, `project_ids`;
- `mod_mcp`: rows `mcp_packages` с `file_ref`, version/enabled/project_ids.

Все три имеют одинаковый lifecycle: row → project filter → workspace materialization. Но они используют разные семантики, разные consumers и часть storage уже вынесена в Content Service (`content_assets`, `content_blob_versions`, aliases/links). Это дублирует понятие файла и заставляет `file_ref` жить внутри JSONB.

### 2.6 Alembic

История показывает последовательную эволюцию:

- `2026082703_modules.py`: каталог, meta и bindings;
- `2026082705_module_company_grants.py` и `2026082901_employee_ui_gaps.py`: дополнительные ownership/binding слои;
- `2026090503_module_instances.py` и `2026090504_backfill_module_instances.py`: snapshot instances и перенос cabinet rows;
- `2026090711_local_global_binds.py`: `bind_kind`, `child_may_edit`, удаление legacy `project_module_bindings`;
- `2026090502_upsert_product_modules.py` и десятки последующих revisions: seed-driven upsert мета.

`alembic/env.py` исключает indexes/unique constraints из drift checks. Более того, product seed migrations вызывают Python upsert, который refresh-ит instance meta для всех owners. Это означает, что schema migration и application product configuration тесно связаны.

## 3. Что хорошо и что уже является правильной основой

- SQLAlchemy/Alembic являются формальным persistence stack для public tables.
- У rows есть уникальный ключ и базовые timestamps.
- Установлен явный `bind_kind=local|global` и `child_may_edit`; это лучше старого `instance_owner`/implicit tab hacks.
- Secret values не должны попадать в row body; используется `secret_ref` и отдельный secret store.
- File blob уже имеет отдельный Content Service с asset/version/blob model; это правильная точка консолидации.
- Workspace — projection/materialization, а не SoT, что соответствует продуктовой модели.
- Chat scope (`all|current`) явно выделен и хранит `session_id`, хотя сейчас policy живет в JSON meta.

## 4. Критические проблемы и coupling

### 4.1 Два row storage одновременно

`module_instance_data_rows` — ORM SoT для API, но `ModuleMaterializeService` все еще создает `{cabinet_schema}.module_data_rows`. Migration `2026090504` импортирует legacy rows в instances, но runtime code продолжает provision/install/uninstall cabinet schemas. Риски:

- две копии одного module data;
- разные cleanup и transaction boundaries;
- migration/backfill может быть повторен или расходиться с live writes;
- schema GC (`cabinet_schema_gc`) имеет эксплуатационную обязанность, не отраженную в module ORM.

### 4.2 Полиморфное владение без FK

`module_instances.owner_kind/owner_id`, `content_asset_links.link_kind/link_id` и ACL resource references не имеют relational FK. Ошибки orphan owner, invalid kind и cross-tenant access ловятся только сервисами.

### 4.3 Snapshot-copy конфликтует с global SoT

При local fork копируются meta и rows. При seed refresh `product_module_upsert.py` обновляет instance meta у всех instances, включая пользовательские копии. В итоге semantics «local instance independent» противоречит фактическому overwrite meta seed-ами. Data rows защищены insert-only seed path лучше, но meta — нет.

### 4.4 Meta fallback и instance documents неоднозначны

Часть путей читает instance doc, затем template fallback; часть bootstrap копирует все template docs в instances. `ModuleMetaDocumentService.put_document()` редактирует template, затем `_mirror_to_owner_instance()` зеркалит platform/company instance. Pod path пишет только resolved instance. Получаются несколько SoT и различное поведение для admin/company/pod.

### 4.5 DSL одновременно schema, UI, workflow и runtime code

`tables/columns` задают схему данных, `views/tabs` UI, `actions` command bus, `materialize` deployment pipeline, `container_env*` secrets/runtime. Все живет в нескольких JSONB blobs и меняется через product seed migrations. Нельзя независимо версионировать API contract, UI contract и migration semantics.

### 4.6 Hard-coded module knowledge

Сильная coupling видна по условиям `module_id == "mod_equipment"`, `table_slug == ...` в generic services и по огромному dispatch в `ModuleActionExecutor`. Equipment delete/index/recompute cascades вызываются из generic row CRUD. Это означает, что добавление доменного модуля требует менять платформенный executor, owner services и routes.

### 4.7 Generic API не выражает relational semantics

API позволяет CRUD любой `table_slug`, но не дает типизированных endpoints, foreign keys, optimistic concurrency, filtering/pagination или domain command contracts. Row uniqueness реализуется путем полного чтения rows и сравнения JSON tokens, а не DB unique indexes. Referential integrity для `ref` полей отсутствует.

### 4.8 Side effects выполняются синхронно после commit

Row write коммитится, затем best-effort запускает actions, OpenSearch indexing, workspace sync scheduling и equipment recompute. Клиент получает иногда уже измененную строку, иногда stale projection. Нет единой outbox/idempotency contract для всех module side effects.

### 4.9 Routes дублируют ownership logic

Admin/company/cabinet/project/pod routes повторяют авторизацию, resolution и CRUD wiring. Pod routes добавляют Bridge scopes, а project runtime routes используют отдельную service path. Это повышает шанс несовпадения policy (например, meta writable/data writable и session filtering).

### 4.10 Alembic seed coupling и drift blind spots

Product configuration обновляется через миграции, импортирующие application seed code. Это удобно для bootstrap, но делает migration output зависимым от текущего Python import graph. `include_object` намеренно игнорирует indexes/uniques, поэтому relational hardening легко не заметить в CI. Старые revisions содержат downgrade с потерей данных и исторические таблицы, которые уже не являются частью current model.

## 5. Что заменить на нормальные relational tables/API

### 5.1 Оставить декларативным

Оставить DSL, но сделать его versioned и typed:

- module UI: `module_views`, `module_tabs`, `module_view_fields`;
- data schema: `module_entities`, `module_entity_fields`;
- actions: `module_actions`, `module_action_params`;
- materialization: `module_materialization_rules`;
- MCP declarations/aliases: `module_mcp_servers`, `module_mcp_tools`, `module_mcp_aliases`;
- environment declarations: typed non-secret env bindings + secret references.

Большие vendor-specific UI extensions можно оставить JSONB `config`, но базовые identity, references, order, enabled/status и lifecycle должны быть колонками/FK.

### 5.2 Ввести единый module definition/version

Предлагаемая модель:

- `module_definitions(id, key, owner_company_id, status, created_at...)` — то, что сейчас `modules`;
- `module_versions(id, module_id, version, source, status, published_at, schema_hash)`;
- `module_entities(id, module_version_id, key, label, storage_kind, chat_scope, project_scope)`;
- `module_fields(id, entity_id, key, type, required, default_json, ref_entity_id, unique_scope, config_json)`;
- `module_views/actions/materialization_rules` с `module_version_id`.

Runtime instance должен ссылаться на конкретную published version. Обновление meta создает новую draft/published version, а не мутирует template и не переписывает пользовательский instance.

### 5.3 Ввести единый binding

Вместо трех application-концепций использовать:

`module_bindings(id, module_id, parent_scope, parent_id, child_scope, child_id, mode, can_edit, status, created_at, revoked_at)`.

Физический SQL может сохранить специализированные FK-таблицы, если PostgreSQL constraints важнее унификации; тогда application service должен иметь один port/repository и один DTO. Не оставлять polymorphic `owner_kind` как единственный integrity mechanism.

`mode` лучше назвать `inherit|fork` (в API временно поддерживать local/global alias). Для inherit не создавать child instance. Для fork создавать instance с `source_instance_id`/`forked_from_version_id`, но дальнейшее чтение не должно неявно merge-ить mutable copies.

### 5.4 Runtime instance и entity records

Заменить generic rows для стабильных entities на:

- `module_instances(id, module_id, owner_scope_id, source_instance_id, version_id, status)`;
- `module_entity_records(id, instance_id, entity_id, project_id nullable, session_id nullable, created_by, version_id, payload_jsonb, created_at, updated_at)` — переходный слой;
- затем доменные таблицы для entities, где есть бизнес-логика и ссылки.

В переходном `module_entity_records` `entity_id` должен быть FK на `module_entities`, а `(instance_id, entity_id, record_key, session_id)` — explicit uniqueness. `payload_jsonb` допустим только для extension fields, не для всех declared columns. Добавить `revision`/`updated_by` и If-Match/ETag API.

### 5.5 Domain tables для equipment

Equipment — не generic CRUD. Минимально выделить:

- `equipment_catalogs` + `equipment_catalog_sources`/credentials reference + indexing status;
- `equipment_request_lines(project_id, session_id, ...)`;
- `equipment_candidate_groups(request_line_id, ...)`;
- `equipment_offers(candidate_group_id, seller_id, source_hash, price_orig, currency, price_rub, is_stale, manual_overrides_jsonb)`;
- `equipment_sellers`, `equipment_web_shops`;
- `equipment_builds`, `equipment_build_slots`, `equipment_build_components`;
- `equipment_procurement_lines`/snapshots;
- `equipment_budget_lines` and explicit source references.

OpenSearch остается read/index projection, а не primary row store. Pipeline принимает domain commands/events и пишет typed tables транзакционно; index refresh идет outbox worker.

### 5.6 Unified content/resource model для prompt/MCP/files

Вместо трех module rows с embedded `file_ref`:

- `content_assets`/`content_blob_versions` остаются blob SoT;
- `workspace_resources(id, owner_scope, owner_id, kind, name, target_path, asset_id, blob_version_id, enabled, metadata_jsonb)`;
- `prompt_profiles(id, resource_scope_id, name, is_default, project_policy)`;
- `prompt_fragments(id, profile_id, target_path, filename, body_asset_id или text, priority, enabled)`;
- `mcp_packages(id, resource_scope_id, name, version, asset_id, enabled)`;
- `resource_project_bindings(resource_id, project_id, mode)` или отдельная typed join table;
- `materialization_entries(resource_id, project_id, workspace_path, kind, generation, status)`.

Prompt text, uploaded files и MCP zip используют один `content_asset`/blob reference. Разница в behavior выражается `kind` и специализированными metadata, а не тремя независимыми `file_ref` formats.

## 6. Как объединить prompt/MCP/files

### Канонический поток

```text
Resource (prompt | file | mcp_package)
    -> ContentAsset / BlobVersion
    -> Scope binding (platform/company/cabinet/project)
    -> Project resource selection
    -> Materialization plan
    -> Workspace projection
```

`project_ids` в JSON body заменить join table. Пустой selection должен быть явной policy (`all_bound`), а не null/empty ambiguity.

### Prompt

`prompt_profiles` — relational profile. `prompt_fragments` — отдельная строка на файл/fragment с `priority`, path, filename и content reference. Merge order (priority, body length, stable id) формализовать в materialization service, но не хранить список файлов в `files_json`.

### Files

Обычный workspace file — `workspace_resources(kind=file)` с `target_path` и blob version. Проверки path, MIME, size и asset ownership выполняются перед записью. Для text prompts можно использовать content asset с inline text или versioned blob, но API должен возвращать общий `resource_ref`.

### MCP

`mcp_packages` — ресурс с ZIP asset, version, enabled и manifest metadata. MCP server/tool aliases — отдельные declarations, а не общая meta blob. Materializer проверяет package manifest, генерирует runtime config и возвращает generation/status.

### Общий API

Новый API-слой должен быть resource-oriented:

- `GET/POST/PATCH /scopes/{scope}/resources`;
- `POST /resources/{id}/versions` и `POST /resources/{id}/publish`;
- `POST /projects/{id}/resource-bindings`;
- `GET /projects/{id}/materialization-plan`;
- `POST /projects/{id}/sync`;
- специализированные `prompts`, `files`, `mcp` endpoints могут быть фасадами над общим resource service.

Старые module routes временно остаются compatibility facade и переводят `table_slug` в entity/resource operations.

## 7. API redesign

### Сейчас

Generic endpoints дублируются по owner и принимают `table_slug + body`. Action invocation получает `action_id + row_id`, а semantics action определяются JSON и hard-coded executor.

### Целевой слой

1. **Module catalog API:** definitions, versions, publication, capabilities.
2. **Binding API:** one binding DTO, explicit mode/can_edit, effective source and status.
3. **Entity API:** `/modules/{module}/entities/{entity}/records` с schema/version, pagination, filters, typed refs, ETag.
4. **Domain API:** equipment/prompts/files/MCP typed commands.
5. **Projection API:** materialization plan/status and idempotent sync.
6. **Action API:** typed command names with validated input/output schemas; generic `invoke` only as compatibility route.

Все endpoints должны возвращать `source_instance_id`, `definition_version`, `projection_generation` при необходимости, чтобы клиент видел, откуда взяты данные.

## 8. Migration plan без big-bang

### Phase 0: inventory and freeze

- Запретить новые writes в cabinet schema `module_data_rows`; оставить read-only fallback только для backfill.
- Снять counts/checksums по `(module, owner, table_slug, row_id)` в обоих stores.
- Составить map всех table slugs и hard-coded consumers.
- Добавить метрики расхождений instance rows vs legacy rows.

### Phase 1: normalize metadata

- Ввести `module_versions` и typed tables/entities/fields/actions/materialization rules.
- Импортировать текущие meta docs без удаления JSONB.
- Runtime читает published typed version; JSONB fallback только для не migrated modules.
- Seed migrations перестают overwrite instance user meta; seeds публикуют новую template version.

### Phase 2: single runtime row store

- Создать `module_entity_records` с FK на definition entity и explicit scope columns.
- Dual-write из текущего `ModuleInstanceService`, затем backfill из `module_instance_data_rows`.
- Переключить list/get на new store, затем write path после проверки parity.
- Отключить `SchemaProvisioner`, cabinet schema installs и GC только после полного zero-read периода.
- Оставить compatibility view/adapter для старых routes.

### Phase 3: bindings/instances

- Ввести unified binding repository/DTO.
- Backfill three binding tables в unified representation.
- Убрать вычисление effective SoT из route/services в один resolver с cache и explicit trace.
- Убрать duplicate instance meta copies; instance хранит version/overrides, а не полную копию template.

### Phase 4: prompt/MCP/files

- Создать resources/profile/fragment/package tables и links к `content_assets`.
- Конвертировать `files_json`, `file_ref`, `mcp_packages` и prompt rows идемпотентным backfill.
- Materialization читает unified resource plan; сравнить workspace manifests/generations.
- После parity удалить generic rows для этих трех modules, но не удалять blobs.

### Phase 5: equipment domain extraction

- Сначала dual-write request lines/groups/offers/catalogs в domain tables.
- Pipeline и OpenSearch переходят на domain repository.
- Budget/procurement snapshots получают FK и audit fields.
- Удалить `mod_equipment` branches из generic row/action services.

### Phase 6: cleanup

- Удалить compatibility tables/columns только отдельными Alembic revisions после retention window.
- Включить Alembic checks для indexes/uniques, добавить constraint tests и migration parity checks.
- Зафиксировать API deprecation и удалить legacy routes после клиентов migration.

## 9. Acceptance criteria

- Один и только один authoritative SQL row store для migrated entities.
- Нет runtime reads/writes в `{schema}.module_data_rows`.
- Каждый record имеет FK на module entity/version и explicit tenant/scope.
- Local fork не изменяется template seed refresh-ом.
- Global bind resolver возвращает trace: binding, effective owner, instance, version, can_edit.
- Prompt/MCP/file resources используют единый asset/version reference и единый project materialization plan.
- Equipment pipeline не импортируется generic row service и имеет typed FK cascades.
- OpenSearch/workspace являются projections с idempotent generation/outbox status.
- Старые routes работают через compatibility adapter, но не содержат собственную access/resolution логику.
- Alembic CI проверяет schema drift, FK/unique/index definitions и backfill parity.

## 10. Краткий verdict

Текущая система — не «плохой ORM», а переходная meta-platform: она быстро дала UI-driven modules, ownership, Pod bridge и materialization, но generic JSONB row store стал универсальным persistence escape hatch. Его следует сохранить только как временный compatibility/entity-extension слой. Нормальная конечная архитектура — typed module definitions + versioned DSL, единый binding/resolver, relational domain tables для business data, unified content resources для prompt/MCP/files и отдельные projection pipelines для workspace/OpenSearch.
