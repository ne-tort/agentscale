# Аудит миграции `mod_equipment` и Commerce crawler

Дата аудита: 2026-11-01. Обследованы Agentscale в этой ветке и доступный read-only checkout `C:\Users\qwerty\git\commerce`. Commerce не изменялся.

## 1. Краткий вывод

`mod_equipment` уже является рабочим доменным модулем Agentscale, а не тонким UI над Commerce. Каноническое состояние подбора и закупки хранится в `module_instances` / `module_instance_data_rows` (JSONB rows), с областью видимости `project` и `session/chat`. OpenSearch — поисковая проекция каталожных предложений; он не должен становиться source-of-truth. Commerce следует сохранить как reference и источник адаптеров ingestion, но не переносить его файловую модель, Telegram UX, второй pipeline или отдельную БД в продуктовый runtime.

Главная целевая граница:

```text
external catalogs / Commerce crawler / S4B
        -> normalized CatalogOfferSnapshot (provenance, freshness, availability)
        -> OpenSearch projection
        -> deterministic matching pipeline
        -> chat-scoped request lines + candidate groups + materialized offers
        -> procurement/budget/tender projections
        -> human review and exports
```

Агент выбирает и объясняет кандидатов, но не является источником цены и не пишет материализованные офферы. Crawler только получает и нормализует внешние данные; он не должен создавать `request_lines`, выбирать best или менять бюджет.

## 2. Что фактически есть в Agentscale

### 2.1 Хранилище и владение данными

- ORM содержит общую платформенную модель модулей в [`modules.py`](../apps/api/src/prodavan/infrastructure/persistence/models/modules.py): `modules`, bindings, `module_instances`, `module_instance_meta_documents`, `module_instance_data_rows`.
- Отдельных SQLAlchemy equipment-таблиц для matching нет. Equipment tables — метаданные и JSONB rows внутри instance.
- `local` bind создаёт собственную копию instance; `global` использует parent SoT. Для `mod_equipment` глобальный cabinet→project bind разрешает агенту писать chat-scoped rows (`child_may_edit=true`). Это корректно для общего каталога/настроек и отдельных результатов конкретного чата, но требует строгой серверной проверки scope и owner.
- Актуальный продуктовый контракт перечислен в [`PRODUCT.md`](PRODUCT.md#L191-L220): каталоги, request lines, groups, offers, procurement, sellers, builds, templates, S4B settings, web products, tenders.

### 2.2 Каталог и OpenSearch

[`equipment_catalog_opensearch.py`](../apps/api/src/prodavan/application/modules/equipment_catalog_opensearch.py) реализует:

- local file и remote PostgreSQL catalog sources;
- source-first reindex: источник открывается до удаления старого индекса;
- один physical index `equipment/c_<catalog-row-id>` на строку `catalogs`;
- progress/status, chunked bulk, Kafka lifecycle events и periodic reindex;
- canonical fields: title, P/N, brand, supplier, price, currency, lead time, stock, `src_hash`;
- `src_hash = sha1(casefold(supplier|title))[:16]` как стабильный идентификатор предложения при wipe/reindex;
- source row key включает P/N и `src_hash`, поэтому два продавца с одним P/N не схлопываются.

Это хорошая read/search projection, но не полноценная сущность offer: в индексе нет audit trail изменения цены, нормального `observed_at`, source version и immutable raw payload. Целевой дизайн должен хранить snapshot/provenance отдельно (в module data или dedicated catalog ingestion store), а OpenSearch строить из него.

Тесты [`test_equipment_catalog_opensearch.py`](../apps/api/tests/unit/test_equipment_catalog_opensearch.py) проверяют стабильность индекса, уникальность поставщиков, source hash, tenancy и lifecycle events. [`test_equipment_catalog_search.py`](../apps/api/tests/unit/test_equipment_catalog_search.py) проверяет local SQLite/remote PostgreSQL, column map, stock denylist и ranking. Старый код/тесты, где `catalog.sqlite` и `EQUIPMENT_*` были доступны внутри Pod, являются удалённой архитектурой и не должны возвращаться.

### 2.3 Matching и закупка

[`equipment_offers_service.py`](../apps/api/src/prodavan/application/modules/equipment_offers_service.py) фиксирует правильное разделение:

- `request_lines` — требования заказчика, chat-scoped;
- `found_groups` — решение агента о группах кандидатов (P/N, aliases, hash aliases, `exact|analog|doubt`, note);
- `found_offers` — только materialized pipeline из OpenSearch, ключ `(group_id, src_hash)`;
- disabled sellers исключаются;
- best offer/group вычисляется автоматикой с учётом точности, цены, priority seller и stale;
- валюты хранятся как original + RUB conversion;
- исчезнувший source становится `is_stale`, цена не подменяется;
- `manual` fields переживают sync;
- `budget_lines` и `procurement` — производные snapshots/aggregates;
- trusted seller registry влияет на seller policy, margin и delivery, но продавец не резолвится из бренда.

Это подтверждено большим набором unit tests [`test_equipment_offers_pipeline.py`](../apps/api/tests/unit/test_equipment_offers_pipeline.py): group matching, disabled sellers, best rules, stale, manual overrides, FX, budget/procurement, legacy orphan offers cleanup.

### 2.4 MCP/agent

Материализуемый first-party пакет [`platform_equipment_mcp.py`](../apps/api/src/prodavan/application/mcp/platform_equipment_mcp.py) сейчас версии `3.3.0`. В нём есть catalog sources/search, request lines, found groups, types/builds, ready builds и tenders; старые `found_offers_*` tools отсутствуют. Это правильная граница: агент пишет требования и candidate groups, а pipeline пишет offers.

Оставшийся риск: MCP tool surface всё ещё смешивает разные bounded contexts (matching, library builds, B2B tenders). Версия 4 должна оставить typed tools только для намерений агента и операций workflow, а read-only derived data отдавать отдельным query API. Любые generic row writes, позволяющие агенту обойти pipeline, должны быть запрещены ACL/validator.

### 2.5 B2B/tenders

Tenders живут как глобальные `mod_equipment` rows, а не отдельные DDL tables. [`b2b/importer.py`](../apps/api/src/prodavan/application/b2b/importer.py) и external API импортируют deterministic rows; [`b2b.py`](../apps/api/src/prodavan/api/v1/b2b.py) даёт list/detail/patch/delete/take-to-work. `tender_attach` копирует плановые позиции в chat-scoped `request_lines` с `tender_id`/`tender_line_id`.

[`equipment_tender_readiness.py`](../apps/api/src/prodavan/application/modules/equipment_tender_readiness.py) правильно отделяет customer text policy от crawler metadata и считает readiness: 70% обычный тендер, 90% при требовании всех позиций. [`test_equipment_tenders.py`](../apps/api/tests/unit/test_equipment_tenders.py) проверяет aggregate counts, coverage, sums, sellers, foreign lines и normalization.

Целевая граница: crawler/importer owns tender intake and immutable source provenance; tender workflow owns lifecycle/relevance/attach; matching owns request lines/groups/offers; budget owns commercial calculation. Не смешивать внешний тендер и внутреннюю закупочную позицию в одной row без ссылок.

## 3. Что найдено в Commerce (read-only)

### 3.1 SearchNet crawler

В `C:\Users\qwerty\git\commerce\searchnet` есть зрелый generic crawler:

- `CrawlService` и frontier strategies `pattern`, `sitemap`, `bfs`, `best_first`, `rel_next`, `load_more`, `auto`;
- robots per host, politeness/rate limits, proxy/session support;
- canonical/content dedup, честные `blocked/captcha/rate_limited`, partial reports;
- long jobs, cancellation и `next_cursor` (in-memory job limitation известна);
- HTTP/Camoufox/Chromium engines;
- MCP tools с `inputSchema`, `outputSchema`, compact/full output и structured errors;
- тесты `test_crawl_patterns`, `test_crawl_discover`, `test_crawl_service`, `test_crawl_api_mcp`, QA wave tests.

Архитектурное описание: `C:\Users\qwerty\git\commerce\searchnet\docs\20-architecture\crawl.md`.

SearchNet — транспортный/extraction слой, а не equipment catalog. Его нельзя напрямую считать ценовой БД: crawl page и extracted text должны пройти adapter/parser/normalizer, который выпускает typed catalog snapshots с source URL, observed time, seller identity, currency, stock and raw provenance. Обязательны robots/ToS, domain allowlist, rate limits, anti-bot status и human review для web offers.

### 3.2 S4B supplier crawler

`commerce/s4b-catalog` содержит специализированный crawler:

- `supplier_service.py` синхронизирует directory/trusted profile и supplier cards;
- `supplier_price_service.py` скачивает price files, проверяет HTML/empty payload, парсит xlsx/csv/xml/zip, дедуплицирует fingerprints и upsert-ит supplier price items;
- Redis queue/state, cooldowns, rate limiting, price slots, stale replacement и Postgres storage.

Это полезный upstream adapter для supplier directory/price snapshots, но он имеет собственную DB, supplier IDs и lifecycle. В Agentscale надо импортировать только versioned normalized snapshots через API/job, а не шарить S4B DB, filesystem или credentials.

### 3.3 Старый Commerce bot/pipeline

Legacy bot держит файловый pipeline `new_run → parse_spec → classify_rows → save_lineitems → search_offers → offers_add_variants → rank_offers → offers_set_best`, `commerce.sqlite`, `runs/*/offers.json`, Telegram commands `/позиции`, `/кп`, global catalogs и local stdio MCP.

Семантически полезны: line item, provenance, explicit source ref, human review, запрет выдуманных цен и `on_order` policy. Переносить storage и workflow как второй параллельный домен нельзя. В Agentscale это уже покрывается `request_lines → found_groups → materialized found_offers → budget/procurement`, Postgres transcript/module rows, OpenSearch projection и Flutter actions.

Существующие [`commerce-boundary.md`](commerce-boundary.md) и [`commerce-semantics.md`](commerce-semantics.md) частично устарели: они описывают несуществующие/неактуальные `specs.*` PG tables и ранние `pipeline.*` mappings. Их следует считать историческим mapping, а не schema contract; актуальный contract — код, migrations, tests и `PRODUCT.md`.

## 4. Целевая доменная модель

### 4.1 Bounded contexts

1. **Catalog source / ingestion** — catalog source, crawl/import run, raw artifact, normalized offer snapshot, supplier identity, freshness and provenance. Owns acquisition and normalization.
2. **Requirement / matching** — tender/request, request line, candidate group, match decision and explanation. Agent-assisted, but validated by platform.
3. **Offer / selection** — materialized offers from current snapshots, best candidate, user selection, stale/manual override. Platform-owned projection.
4. **Commercial calculation** — budget line, supplier margin/delivery, procurement aggregate, exports. Deterministic and human-editable where allowed.
5. **B2B intake** — external tender, lifecycle/relevance, source refs, planned lines, attachment to a project/chat. Does not own prices.
6. **Agent workflow** — MCP intents, prompts, task progress and audit. Does not own domain facts.

### 4.2 Proposed canonical entities

- `CatalogSource`: owner/scope, kind (`file|remote_db|s4b|web_crawl|api`), credentials reference, mapping/parser version, enabled state.
- `IngestionRun`: source, started/finished, status, cursor, counters, warnings, source version/hash.
- `CatalogOfferSnapshot`: immutable or append-only normalized offer observations: `offer_key`, supplier, title, brand, P/N, SKU, price_original/currency, availability, lead time, source_ref, observed_at, valid_until, raw_ref, run_id. `src_hash` may remain a compatibility key but must not be the only identity forever.
- `CatalogOfferCurrent`: optional compact current projection used to build OpenSearch; no business selection fields.
- `Tender`: external source identity, customer texts, deadline, lifecycle/relevance/work status, provenance.
- `RequirementSet`/`RequestLine`: project/chat/tender linkage, title, P/N, qty, constraints, source provenance.
- `CandidateGroup`: line linkage, aliases, `match_kind`, agent note/confidence. This is the agent decision.
- `OfferMatch`: group + snapshot/current offer, computed match rationale/rank, stale flag, manual overrides.
- `Selection`: explicit human choice per requirement line; never infer from UI sort alone.
- `ProcurementPlan`/`BudgetLine`: selected offer snapshot, qty, margin source, delivery, calculated totals and export version.

For an incremental migration, these can initially remain JSONB module rows. The model must nonetheless expose stable IDs, explicit links, provenance and version semantics. Move high-volume immutable snapshots and audit history to dedicated relational tables only when query volume/retention requires it.

## 5. Source-of-truth and crawler boundary

| Data | SoT | OpenSearch | Crawler may write? |
|---|---|---|---|
| source config/credentials ref | Agentscale catalog source | no | via authenticated adapter command |
| raw downloaded file/page | object store + ingestion run | no | yes, immutable artifact |
| normalized offer observation | ingestion snapshot store | indexed projection | yes, append/upsert by source key |
| current searchable offer | derived from snapshots | yes | indirectly via reindex |
| request line | module row / future requirement table | no | no |
| candidate group | module row, agent decision | no | no |
| found offer | deterministic pipeline projection | no (or separate result index) | no |
| selected offer | module row, human action | no | no |
| budget/procurement | deterministic module projection + manual fields | no | no |
| tender intake | B2B intake row | no | external adapter only |

Rules:

- Every web/S4B price needs `source_ref`, `observed_at`, source run and availability semantics.
- No price from model memory or page text bypassing normalizer.
- `on_order`/unknown availability is policy-filtered, not silently treated as in stock.
- Reindex is replayable from raw/normalized source snapshots; a failed source keeps the last good projection.
- A crawler failure/block is visible and does not delete the last valid offer; freshness/stale policy decides eligibility.
- External credentials stay in Vault/secret reference; no Commerce `.env`, shared FS or DB mounts.

## 6. Agent, MCP and UI workflow

### Agent/MCP target

1. Agent receives a request/tender context and calls `request_lines_*`.
2. Agent calls catalog search/read-only tools and records `found_groups_upsert` with exact/analog/doubt and explanation.
3. Platform starts idempotent materialization job; agent cannot call `found_offers_upsert`.
4. Agent reads offers and explains tradeoffs; it may propose selection, but human/API action commits selection.
5. Agent can call tender intake/readiness tools and export actions, but cannot mutate crawler status, seller trust, price or calculated totals.
6. MCP responses include IDs, freshness, source refs, stale/block warnings and next action; no giant raw catalog dumps.

Split MCP packages eventually into `equipment-matching`, `equipment-tenders`, `equipment-catalog-read`, while keeping compatibility aliases during migration. Enforce table/action ACL server-side, not only in tool descriptions.

### UI target

1. **Data → Sources**: configure file/remote/S4B/web source, mapping, schedule, status, last successful run, warnings and freshness.
2. **Data → Requests**: import specification or take tender to work; edit request lines.
3. **Data → Candidates**: show groups and evidence; exact/analog/doubt visibly distinct; refresh prices is explicit.
4. **Data → Offers/Selection**: materialized offers, stale/source/freshness badges, one selected offer per line, human override audit.
5. **Data → Procurement/Budget**: supplier aggregate, margin/delivery, totals and exports; derived rows clearly separate manual inputs.
6. **Data → Tenders**: intake, relevance, lifecycle, coverage/readiness, attach to work.
7. **Management → Sellers/Templates**: trusted seller policy and document templates, not hidden in crawler settings.

UI must not expose OpenSearch index names, source DB credentials, raw crawler internals or a second Commerce-like run filesystem. `on_load` refresh may reconcile projections, but must be bounded/idempotent and never silently change a manual selection.

## 7. Что оставить, переписать, выкинуть

### Оставить

- JSONB module/bind/scope infrastructure as current migration substrate.
- `catalogs` source configuration and source-first OpenSearch indexing.
- `src_hash` compatibility key, currency/FX, stale and manual override semantics.
- `request_lines` + `found_groups` agent contract.
- deterministic offers pipeline, trusted seller policy, budget/procurement aggregates.
- tender attach/readiness and external API boundaries.
- SearchNet crawl primitives and S4B parsers as isolated adapters, subject to typed output/provenance.
- Agentscale MCP gateway, Pod isolation, Flutter data hub and document exports.

### Переписать/консолидировать

- Add ingestion-run/snapshot provenance and freshness instead of only a live OS document.
- Make `offer_key` source-stable and separate from display hash; support price history.
- Consolidate web products into the same normalized catalog-offer pipeline. `web_products` may remain a TTL staging table, but must not be a parallel offer model.
- Make tender planned lines a first-class requirement import with explicit immutable source link, then attach/copy into chat context by reference.
- Split typed MCP/read APIs by context and remove generic mutation paths.
- Update migration docs to current module JSONB model; mark old `specs.*` mapping deprecated.

### Выкинуть / не переносить

- Commerce Telegram bot, commands, global user/session state and Telegram-specific UX.
- Commerce `commerce.sqlite`, `runs/*` as product SoT, `offers.json`/`selection.json` as parallel canonical stores.
- Pod access to `catalog.sqlite`, `EQUIPMENT_*` env and shared Commerce filesystem.
- Agent-written materialized offers and direct best-price mutation.
- Duplicate Commerce `lineitems → variants` domain inside Agentscale.
- Crawler-owned matching, ranking, budget, KPI or tender readiness decisions.
- Blind web scraping without robots/allowlist/provenance/block handling.
- Separate source-specific seller truth that bypasses Agentscale trusted seller policy.
- Automatic deletion of current offers on a transient crawler outage.

## 8. Migration sequence and acceptance criteria

1. Freeze current Agentscale contracts and mark `docs/08-migration/commerce-*.md` historical.
2. Introduce typed `CatalogOfferSnapshot` adapter contract and import one Commerce/S4B fixture without changing Commerce.
3. Index snapshots into existing equipment OS projection; verify same `src_hash`, currency and stock behavior.
4. Replay request lines/groups and compare materialized offers/best/selection semantics, not JSON byte equality.
5. Migrate web products to normalized source path; keep TTL only for unverified web observations.
6. Add provenance/freshness in UI and MCP, then enforce ACL removing generic offer writes.
7. Pilot one cabinet: source refresh → matching → manual selection → procurement → tender readiness → export.
8. Freeze Commerce writes, keep read-only/reference, then archive after parity and rollback window.

Acceptance checks:

- two suppliers with same P/N remain two offers;
- source outage preserves last good snapshot and marks stale/warning;
- price changes compare original currency and preserve manual overrides;
- `on_order` never enters eligible stock result;
- every offer shown in UI has source/refreshed-at metadata;
- agent can create lines/groups but cannot create or alter materialized offer price;
- tender aggregate ignores foreign lines and preserves manual tender fields;
- OpenSearch can be dropped and rebuilt from snapshots;
- no Commerce DB/filesystem/secret is mounted in an Agentscale Pod.

## 9. Evidence index

- Product/current module contract: [`PRODUCT.md`](PRODUCT.md#L191-L220).
- Module persistence: [`modules.py`](../apps/api/src/prodavan/infrastructure/persistence/models/modules.py).
- Catalog indexing: [`equipment_catalog_opensearch.py`](../apps/api/src/prodavan/application/modules/equipment_catalog_opensearch.py).
- Matching/materialization: [`equipment_offers_service.py`](../apps/api/src/prodavan/application/modules/equipment_offers_service.py).
- MCP package: [`platform_equipment_mcp.py`](../apps/api/src/prodavan/application/mcp/platform_equipment_mcp.py).
- Pipeline tests: [`test_equipment_offers_pipeline.py`](../apps/api/tests/unit/test_equipment_offers_pipeline.py), [`test_equipment_catalog_opensearch.py`](../apps/api/tests/unit/test_equipment_catalog_opensearch.py), [`test_equipment_catalog_search.py`](../apps/api/tests/unit/test_equipment_catalog_search.py).
- Tender policy/tests: [`equipment_tender_readiness.py`](../apps/api/src/prodavan/application/modules/equipment_tender_readiness.py), [`test_equipment_tenders.py`](../apps/api/tests/unit/test_equipment_tenders.py).
- Commerce crawler: `C:\Users\qwerty\git\commerce\searchnet\docs\20-architecture\crawl.md` (read-only), `C:\Users\qwerty\git\commerce\s4b-catalog\src\s4b_catalog\crawl\supplier_service.py`, `supplier_price_service.py` (read-only).
- Historical boundary/mapping: [`commerce-boundary.md`](commerce-boundary.md), [`commerce-semantics.md`](commerce-semantics.md); update before using as implementation contract.
