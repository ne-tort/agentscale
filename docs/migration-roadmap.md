# Agentscale Migration Roadmap

Статус: рабочий план миграции.
Дата: 2026-11-01.
Основание: migration audits + architecture каркас.

## 1. Правила исполнения

1. Работа ведётся только в `migration/agent-sandbox` и `wt-migration`.
2. До отдельного решения не коммитить и не отправлять migration-изменения в `main`, dev или prod.
3. Не использовать shared dev как preview миграции.
4. Каждая фаза должна иметь runnable local checks и acceptance criteria.
5. Новые доменные функции не строятся на старом generic DSL API.
6. Legacy код удаляется после подтверждённого cutover, а не сохраняется бессрочно ради компиляции.
7. Документация описывает каркас и инварианты; детали API/таблиц фиксируются рядом с реализацией и тестами.

## 2. Порядок зависимостей

```text
P0 inventory and contracts
  -> P1 platform runtime revision + resource model
      -> P2 typed API + React shell
          -> P3 resources + dynamic UI foundation
              -> P4 prompt/MCP/files materialization
                  -> P5 equipment domain vertical slice
                      -> P6 automation/approval/webhooks
                          -> P7 cutover and legacy removal
```

Equipment и frontend могут развиваться параллельно после P1, но ни один из них не должен обходить runtime revision, content/resource ACL и typed API contracts.

## 3. Phase 0: Freeze, inventory, decisions

### Цель

Остановить расширение старой архитектуры и получить проверяемую карту текущего продукта.

### Работы

- Заморозить добавление новых DSL tables/views/actions для новых features.
- Зафиксировать список всех current module slugs, rows, actions, meta documents и hard-coded consumers.
- Зафиксировать routes, Dart API calls и фактические FastAPI routers.
- Отдельно пометить current `docs/08-migration/*` mapping-документы как historical, если они расходятся с кодом.
- Составить data census для `module_instance_data_rows` и legacy cabinet schema rows.
- Ввести read-only diagnostic для расхождений двух row stores.
- Определить domain ownership matrix: core, agent platform, content/resources, equipment, tenders, automations.
- Зафиксировать API versioning policy и canonical capability naming.

### Выход

- inventory без неизвестных critical consumers;
- список legacy paths, которые запрещено расширять;
- ADR по capability/package model;
- ADR по resource/content model;
- ADR по runtime revision;
- ADR по equipment boundaries;
- локальный disposable test setup.

### Блокеры выхода

- Неизвестные записи, которые нельзя классифицировать.
- Неясно, какая БД является source of truth.
- Новый feature всё ещё требует правки generic executor для доменной логики.

## 4. Phase 1: Runtime foundation

### Цель

Сделать проектный runtime воспроизводимым до миграции UI и equipment.

### Backend

Ввести концептуальные и persistence сущности:

- `module_versions` / published capability metadata;
- `effective_module_bindings` resolver output;
- `project_runtime_revisions`;
- `capability_bundles` или content-addressed revision manifests;
- materialization entries/status;
- desired/applied revision fields;
- revision-aware bridge claims and health response.

Implement two steps:

```text
prepare(project) -> validate -> immutable revision
apply(revision)   -> workspace/config/secret lease/pod
```

Сделать apply idempotent. Частичная materialization переводит revision в `failed`, а не в `ready`.

### Security

- убрать literal Bridge token из Pod env;
- сделать short-lived per-Pod secret lease/projected credential;
- проверять FileRef ACL/existence до prepare;
- включить module capability/version в scope and audit context;
- запретить Pod direct connections к tenant databases и crawler secrets.

### Runtime delivery

- persist runtime image digest;
- перейти для migration preview на immutable tags;
- добавить bundle/revision diagnostics endpoint;
- readiness должен проверять applied revision и capability completeness.

### Выход

Один существующий project может создать revision, применить её, пережить restart/reload, показать digest/provenance и восстановиться после failed apply.

## 5. Phase 2: Typed API and React shell

### Цель

Запустить первый production-quality web shell без equipment и без DSL runtime.

### Frontend stack

- React + TypeScript + Vite;
- React Router или аналогичный URL-first router;
- TanStack Query или эквивалентный server-state cache;
- generated OpenAPI DTOs после обновления live schema;
- Zod/Valibot boundary validation;
- Playwright browser tests;
- shared design system with accessible primitives.

### Первый vertical slice

```text
login -> tenant/cabinet -> projects list -> project details
      -> container status -> session list -> chat stream
```

Не переносить сразу все admin/data screens. Сначала доказать:

- refresh/reload сохраняет URL и context;
- auth refresh/401 работает;
- chat SSE append-only и reconnect корректны;
- transcript/run projection совпадает с backend events;
- files/attachments имеют typed API boundary;
- error/loading/empty states полноценны.

### API work

- убрать reliance на skeletal `openapi.yaml`;
- генерировать contract из live FastAPI app;
- typed SSE/multipart/download clients писать вручную поверх generated DTOs;
- новые endpoints делать resource/domain-oriented;
- compatibility facade для старого Flutter не расширять.

### Выход

React shell работает локально против API/stub backend и покрывает один полный workflow. Flutter остаётся только временным старым клиентом.

## 6. Phase 3: Dynamic UI foundation

### Цель

Сохранить исходную идею пользовательских динамических интерфейсов, но сделать её безопасной: сначала declarative apps, затем isolated code plugins, оба режима поверх capability gateway.

### Stage A: declarative apps

- typed view/schema definitions;
- routes, forms, tables and actions из allowlisted primitives;
- generated SDK с capability types;
- tenant/project-scoped app definitions;
- app version/publish/rollback;
- query/action quotas and audit;
- no arbitrary JS and no direct infrastructure queries.

### Stage B: code plugins

- isolated iframe on a separate origin;
- sandboxed runtime and strict CSP;
- `postMessage`/RPC protocol с schema validation;
- opaque short-lived capability handle instead of raw auth token;
- signed immutable bundle built by a dedicated worker;
- dependency allowlist, size/time limits, kill switch and rollback;
- tenant admin approval before publication or capability grant.

### Capability gateway requirements

Every call rechecks identity, tenant/cabinet/project scope, user role, resource ACL, capability grant, input schema, quotas, concurrency, audit policy and approval requirement. The plugin manifest is a request, not a permission.

Generic `sql.query`, `redis.command`, `kafka.produce`, `mongodb.find`, `opensearch.request` and `kubernetes.exec` are forbidden for tenant plugins. Infrastructure is exposed only through typed domain capabilities or a restricted read-only query AST.

### Выход

- declarative tenant app renders in a separate route and cannot bypass ACL;
- deny/replay/cross-project tests pass;
- code plugin can be loaded in an isolated origin and call only granted typed capabilities;
- plugin compromise does not expose shell tokens, other tenants or infrastructure credentials.

## 7. Phase 4: Unified resources and materialization

### Цель

Перенести prompts, MCP и files из трёх DSL modules в единый resource lifecycle.

### Data model

- content asset/blob version остаётся immutable SoT;
- workspace resource имеет kind, scope, target path, enabled and version;
- prompt profile/revision and fragments;
- MCP package manifest/version/digest;
- explicit project resource binding;
- materialization provenance and generated path status.

### Behavior

- profile is published/versioned;
- each run records profile revision;
- explicit selection policy replaces ambiguous `project_ids=null` semantics;
- prompt composition deterministic and inspectable;
- MCP package manifests validated before apply;
- generated files are pruned only according to revision-owned manifest;
- module resource changes create a new project runtime revision.

### UI

React screens:

- Agent profile;
- Prompt sources/fragments;
- Files/resources;
- MCP servers/tools and permissions;
- materialization plan/status;
- sync/revision history.

### Выход

Один базовый profile можно подключить к project, изменить, увидеть diff/provenance, применить новую revision и rollback на предыдущую.

## 8. Phase 5: Equipment vertical slice

### Цель

Сделать новую предметную модель подбора техники на одной сквозной цепочке, не перенося старые DSL tables как есть.

### Рекомендуемый первый slice

```text
IMAP or S4B import
  -> ingestion run + raw artifact
  -> normalized catalog offers
  -> exact part-number search
  -> customer request line
  -> candidate group
  -> deterministic offers projection
  -> human selection
  -> procurement line
  -> reviewable export
```

Сначала один source adapter и один export format. Web search, complex builds and all supplier parsers идут после доказательства model.

### Ownership

- ingestion adapter: fetch, parse, source freshness, raw artifact;
- catalog service: normalized product/offer snapshots;
- matching service: request lines/candidate groups/offer candidates;
- procurement/budget: selection, margins, delivery, totals;
- tender service: import/lifecycle/relevance/attach;
- agent: classification, candidate intent, explanations;
- UI: review and overrides.

### Invariants

- same P/N from two sellers = separate offers;
- stale last-good snapshot remains visible with warning;
- `on_order` is not silently treated as available;
- every price has currency, observed_at, source_ref, run_id;
- OpenSearch can be deleted and rebuilt;
- agent cannot write authoritative price/availability;
- no Commerce DB/FS/env access from Pod;
- tender data is not hidden in generic module row body.

### Migration strategy

- import current JSONB equipment rows into staging tables only for analysis;
- build fixtures from real representative cases;
- dual-read only for parity diagnostics, not indefinite dual-write;
- cut new UI to typed equipment API;
- cut materialization/MCP to new services;
- archive/remove old `module_action_executor` branches after zero-read period.

## 9. Phase 6: Agent operations

### Target features

- `agent_runs` separate from sessions;
- run timeline and trace view;
- durable approvals;
- automation definitions and scheduled runs;
- inbound webhooks with signature/idempotency;
- outbound webhook deliveries with retries/backoff;
- cancellation and timeout state machine;
- artifact links and usage/cost projections;
- subagent runs as child runs/events.

### Implementation order

1. run model and event envelope;
2. durable queue/lease/idempotency;
3. approval state machine;
4. webhook ingress and delivery;
5. schedules/automation UI;
6. optional visual authoring that compiles to typed automation definitions.

Canvas is optional authoring UI, never runtime source of truth.

## 10. Phase 7: Remove legacy

Удалять только после подтверждения:

- zero writes to legacy cabinet schema rows;
- zero reads in production-like local suites;
- no new callers of generic `table_slug` routes;
- equipment features use typed services;
- React shell covers required workflows;
- runtime revision applied/rollback tests green;
- import snapshots and rollback backups verified.

Candidate removals:

- Flutter production shell;
- generic equipment branches from `ModuleActionExecutor`;
- legacy cabinet schema provisioner/row store;
- duplicated binding route logic;
- old module seed overwrite behavior;
- JSONB equipment rows and old virtual OpenSearch row façade;
- compatibility routes after announced deprecation window.

## 11. Preview and testing strategy

### Local

- API pytest with disposable Postgres;
- Alembic upgrade/check and migration smoke;
- worker tests with fake Redis/queue;
- OpenSearch projection rebuild tests;
- React typecheck/lint/unit/Playwright;
- kustomize render-only validation;
- container image smoke with immutable local tags.

### Migration preview, later

Preview must be separate from shared dev:

- unique Argo application and namespaces;
- immutable API/web/agent image tags;
- isolated Postgres/PVC/object storage/Redis/Kafka/Mongo/OpenSearch/Keycloak;
- explicit host/ingress;
- dedicated RBAC/NetworkPolicy;
- manual or separate-repo Argo bootstrap;
- cleanup/TTL policy.

Do not add preview Application under current main root-app until its branch-discovery and isolation behavior are deliberately designed.

## 12. Definition of done for a migration slice

A slice is complete only when:

- domain/API/UI contract is typed;
- permissions and tenant scope are tested;
- event/run behavior is idempotent;
- local tests and browser/API smoke pass;
- materialization/revision impact is explicit;
- observability and audit fields exist;
- no new legacy coupling was introduced;
- rollback or data recovery path is documented;
- code is ready for a future migration PR, but not automatically pushed to dev.

## 13. First implementation backlog

1. Add API contract extraction from live FastAPI routes and compare to Dart client usage.
2. Define `ProjectRuntimeRevision` schema and prepare/apply service interfaces.
3. Add capability manifest schema with version/dependency/conflict fields.
4. Add resource/content DTOs and project resource binding API.
5. Scaffold `apps/web` shell with auth, routing, API client and chat protocol test.
6. Build basic profile/resources page using typed API, not DSL interpreter.
7. Create equipment relational schema draft and import fixture generator.
8. Implement one S4B/IMAP ingestion adapter behind typed source interface.
9. Implement normalized offer snapshot plus rebuildable search projection.
10. Add run/event/approval foundation before automation UI.

Эти пункты являются стартовым backlog, а не обещанием сохранить текущие names/routes. При реализации допускается и ожидается пересмотр деталей при обнаружении расхождений с живым кодом.
