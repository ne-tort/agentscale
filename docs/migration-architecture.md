# Agentscale Migration Architecture

Статус: рабочий архитектурный каркас, не финальный implementation contract.
Дата: 2026-11-01.
Область: полная миграция UI, модульной модели, equipment-домена и agent control plane.

## 1. Цель

Agentscale должен стать профессиональной SaaS-платформой для изолированных AI-workspaces:

```text
tenant -> cabinet -> project -> Pod workspace -> agent session -> run -> events/artifacts
```

Платформа должна давать пользователю не «чат с JSON-таблицами», а рабочее пространство агента:

- проекты и контейнеры;
- durable sessions и runs;
- файлы и артефакты;
- MCP/tools с видимыми разрешениями;
- prompt/agent profiles;
- approvals и audit;
- automations, webhooks и фоновые задачи;
- доменные приложения, включая подбор техники и закупку.

## 2. Главные решения

### 2.1 Frontend

Целевой стек: React + TypeScript + Vite.

Рекомендуемая структура:

```text
apps/web/
  app/                 routing, auth, providers, shell
  features/            chat, projects, files, runs, approvals, settings
  modules/             built-in capability UI packages
  shared/api/          generated DTOs + typed hand-written SSE clients
  shared/ui/           design system
  shared/state/        query cache and explicit context stores
```

Клиент строится URL-first и API-first:

- OpenAPI генерируется из живых FastAPI routers, а не из текущего skeletal-файла;
- DTO типизированы, входы валидируются runtime schema (Zod или аналог);
- server state хранится через query cache/invalidation;
- локальное состояние immutable и явно scoped;
- polling и streaming централизованы;
- chat transcript реализуется reducer/state machine;
- domain pages являются обычными React screens, не интерпретаторами DSL.

Нельзя переносить:

- Flutter widgets;
- ChangeNotifier/InheritedWidget как архитектурный паттерн;
- giant API client;
- mutable ambient work context;
- equipment-specific branches внутри generic table renderer;
- widget-stack navigation;
- generic `Map<string, dynamic>` как публичный API contract.

### 2.2 Module и package model

Module не должен быть произвольным UI-кодом, загружаемым из БД или из Pod.

Целевая модель:

```text
Capability package (versioned code)
  -> backend manifest + typed API/services
  -> frontend package registered at build time
  -> prompt/MCP/file contributions
  -> permissions, triggers, artifacts and migrations
```

Динамически включаются:

- entitlement/installation;
- navigation entries;
- feature flags;
- profile/resource bindings;
- MCP servers/tools;
- prompt fragments;
- files and artifacts;
- automation definitions;
- permissions.

Не загружается динамически:

- произвольный JavaScript из БД;
- произвольный Python из Pod;
- неподписанный executable package;
- доменная логика через `action.kind` dispatch.

Для v1 все frontend capability packages входят в основной web build и регистрируются через registry:

```ts
registerCapability({
  id: "equipment",
  version: "1.0.0",
  routes,
  navigation,
  queries,
  commands,
  permissions,
});
```

Позже возможен remote/federated module delivery, но только с подписанными артефактами, compatibility checks, CSP и rollback. Это не часть первой миграционной волны.

### 2.3 Backend module registry

Сохраняется понятие module/capability, но меняется его смысл.

```text
module_definition
  -> module_version (draft/published/deprecated)
      -> typed declarations
      -> backend service registration
      -> UI capability metadata
      -> MCP/prompt/file contributions
      -> migration and compatibility metadata
```

DSL может временно использоваться как authoring/import format, но runtime работает с нормализованной versioned model. DSL не является публичным доменным API.

Базовые декларации:

- entities/fields and references;
- views/navigation metadata;
- typed commands;
- materialization rules;
- MCP servers/tools and policy;
- env bindings and secret references;
- dependencies/conflicts/provides;
- capability permissions.

Identity, version, enabled/status, order, references, scope и lifecycle должны быть SQL-колонками и FK. JSONB остаётся только для расширений, vendor metadata и редких config blocks.

### 2.4 Ownership и bindings

Семантика `inherit`/`fork` сохраняется, но должна иметь один resolver/repository.

```text
EffectiveModuleBinding
  project_id
  module_id
  capability_version
  source_instance_id
  source_owner_scope
  mode: inherit | fork
  can_edit
  selected_resources
  policy_snapshot
```

Три старые binding tables могут временно остаться физически, но приложение должно видеть единый port. Новая модель должна иметь явные scope references и не полагаться только на polymorphic `owner_kind/owner_id` без проверки существования владельца.

Правила:

- `inherit` не создаёт mutable child instance;
- `fork` создаёт независимую instance с зафиксированной source/version;
- published version не переписывает пользовательскую instance;
- effective resolution выполняется один раз при построении runtime revision;
- клиент видит source instance, version и projection generation.

### 2.5 Resources: prompts, MCP, files

Prompt/MCP/files объединяются не в одну «таблицу строк», а в единый resource/content domain.

```text
content_asset
  -> blob_version
  -> workspace_resource(kind)
  -> scope/project binding
  -> materialization plan
  -> project runtime revision
```

Целевые сущности:

- `content_assets`, `content_blob_versions` — immutable content SoT;
- `workspace_resources` — resource kind, name, target path, asset/version, enabled;
- `prompt_profiles` — named/versioned profile;
- `prompt_fragments` — path, filename, priority, content reference;
- `mcp_packages` — package version, manifest, digest, asset reference;
- `resource_project_bindings` — explicit project selection/policy;
- `materialization_entries` — generated path, generation, status, provenance.

Профиль агента должен иметь immutable published revision. Каждый run хранит effective profile revision.

Secrets никогда не являются content resource body. Они представлены secret references/leases и подаются через short-lived credential mechanism.

### 2.6 Runtime revision and capability bundle

Главный runtime-контракт — immutable `ProjectRuntimeRevision`.

Он создаётся при launch/sync/reload и включает:

- effective module bindings and versions;
- dependency graph;
- prompt contributions and generated file hashes;
- MCP package locks and digests;
- workspace file entries and provenance;
- non-secret env snapshot/hash;
- secret binding descriptors, без значений;
- Bridge scopes and action policy version;
- provider/model runtime config;
- agent-runtime image digest;
- workspace ownership/prune manifest.

Состояния:

```text
planned -> validated -> applying -> ready
                   \-> failed
ready -> superseded
```

Проект хранит `desired_revision_id` и `applied_revision_id`. Старые `materialize_manifest`, `workspace_outdated_at` и `hydrate_generation` остаются compatibility projections на время миграции.

Materialization должен быть fail-closed: ошибка обязательной операции делает revision failed, а не «успешной с warning». Повторное применение идемпотентно по revision/bundle digest.

Bridge token не должен лежать literal env в Pod. Используется projected Secret/short-lived credential с независимой rotation/revocation.

### 2.7 Agent control plane

Разделяются:

```text
Session  = durable conversational context
Run      = one execution/turn/automation invocation
Event    = append-only fact
Projection = transcript, metrics, trace, UI state
```

Минимальные сущности:

- `agent_sessions`;
- `agent_runs`;
- `agent_events`;
- `agent_artifacts`;
- `approvals`;
- `automation_definitions`;
- `automation_runs`;
- `webhook_endpoints`;
- `webhook_deliveries`;
- trace/usage projections.

Automation и webhook не являются скрытым chat message:

- вход проверяется и быстро enqueue-ится;
- есть idempotency key;
- очередь claim/lease/retry/dead-letter;
- tenant quotas and concurrency policy;
- run detail и audit доступны в UI.

Approval — durable state machine:

```text
running -> waiting_approval -> running
                         \-> rejected / expired
running -> cancel_requested -> cancelled
```

В approval snapshot входят policy, arguments, scope, actor, expiry и audit metadata.

### 2.8 Equipment bounded context

`mod_equipment` переписывается как отдельный доменный context, а не generic DSL module.

Границы:

```text
Ingestion -> Catalog -> Matching -> Procurement/Budget -> Tender readiness -> Export
```

Основные сущности:

- `equipment_sources` and `ingestion_runs`;
- `equipment_catalogs`;
- `catalog_items` / normalized product identity;
- `catalog_offers` / price snapshots;
- `suppliers` and source accounts;
- `equipment_request_lines`;
- `candidate_groups`;
- `offers` with source/provenance/freshness;
- `tenders`, `tender_items`, lifecycle and attach state;
- `builds`, `build_slots`, compatibility rules;
- `procurement_lines`;
- `budget_lines` and export snapshots.

OpenSearch — rebuildable read projection. Источник истины — normalized PostgreSQL snapshots/history plus raw artifacts in object storage.

S4B crawler and IMAP price ingestion are first-party adapters/workers inside this context. B2B-Center importer owns tender intake/lifecycle. Commerce repositories are not shared by DB, filesystem, `.env` or direct mounts.

Adapter contract:

```text
source -> typed authenticated import
      -> raw artifact + source_ref
      -> normalized snapshot
      -> provenance, observed_at, run_id, freshness
      -> deterministic index projection
```

Agent responsibility:

- parse/classify customer request lines;
- create candidate groups and matching intent;
- explain uncertainty and request review.

Agent must not:

- write authoritative price/availability;
- copy catalog offers into the database;
- choose a final commercial offer without policy/review;
- access crawler credentials or direct DB.

Canonical UI workflow:

```text
Sources -> Requests/Tenders -> Candidates -> Offers/Selection
         -> Procurement/Budget -> Review -> Export
```

### 2.9 UI shell

Web shell owns:

- auth and tenant/cabinet/project context;
- URL routing and guards;
- workspace navigation;
- project/container status;
- session list and chat;
- files/resources/profile surfaces;
- run/approval/automation operations;
- module registry and entitlement checks.

Capability packages own:

- domain routes and pages;
- typed queries/commands;
- domain tables/forms;
- module-specific empty/loading/error states;
- MCP/prompt/resource settings relevant to the domain.

Equipment pages must not leak OpenSearch indices, JSONB rows, or crawler implementation details into the main shell.

## 3. Dynamic frontend extensibility

### 3.1 Решение

Пользовательский динамический интерфейс допускается как отдельный продуктовый слой. Но «любой frontend-код» не должен выполняться внутри privileged shell и не должен получать прямые credentials к PostgreSQL, Redis, Kafka, MongoDB, OpenSearch, MinIO или Kubernetes.

Целевая схема:

```text
User UI package / app definition
  -> registry + validation + version + policy review
  -> isolated browser sandbox
  -> capability SDK
  -> backend capability gateway
  -> ACL/RLS + quota + rate limit + audit
  -> typed domain/infrastructure adapter
```

Нужно разделить три уровня:

1. **Platform shell** — доверенный React-код Agentscale. Auth, tenant context, routing boundary, security headers, capability broker и navigation host.
2. **Dynamic app/plugin** — код пользователя или tenant-а. Выполняется в sandboxed iframe/isolated origin или в другом эквивалентном isolate. Не получает shell token, DOM shell, произвольный network access или secrets.
3. **Capability gateway** — единственная точка доступа к данным и операциям. Проверяет каждый запрос независимо от того, пришёл он из React shell, dynamic plugin, MCP или Pod.

### 3.2 Формы динамичности

Нужны два режима, потому что они имеют разный риск:

**Declarative UI mode (default):**

- JSON/typed schema описывает routes, forms, tables, panels, actions и data queries;
- React host рендерит только allowlisted components;
- backend возвращает typed view model и capability manifest;
- arbitrary JavaScript не нужен;
- подходит большинству пользовательских кабинетов и business screens.

**Code plugin mode (advanced):**

- пользователь загружает versioned JS/TS bundle;
- bundle собирается в отдельном build service, а не исполняется из сырого исходника;
- разрешённый runtime API предоставляется через SDK;
- plugin живёт на отдельном origin и в sandboxed iframe с CSP;
- plugin communication идёт через typed `postMessage` protocol или equivalent RPC;
- plugin получает только opaque app/session handle, никогда не raw access token;
- публикация требует validation, dependency policy, size/time quotas и version pinning.

Dynamic remote module federation в privileged shell не является базовым режимом: ошибка или supply-chain compromise в одном plugin должна быть неспособна читать shell state и вызывать platform APIs в обход gateway.

### 3.3 Capability manifest

Каждый dynamic app/plugin публикуется вместе с immutable manifest:

```json
{
  "app_id": "equipment-review",
  "version": "1.2.0",
  "runtime": "sandbox-iframe-v1",
  "entrypoint": "sha256:...",
  "requested_capabilities": [
    "equipment.requests.read",
    "equipment.offers.read",
    "equipment.offers.select",
    "files.download"
  ],
  "routes": ["/equipment/review"],
  "limits": {
    "requests_per_minute": 120,
    "max_concurrent_queries": 4,
    "max_response_bytes": 1048576
  },
  "data_policy": {
    "project_scope": "current",
    "session_scope": "optional"
  }
}
```

Manifest не является разрешением. Это только requested contract. Effective grant создаётся отдельно:

```text
plugin requested capabilities
  -> tenant policy
  -> cabinet/project grants
  -> user role
  -> resource ACL
  -> quota plan
  -> effective capability token
```

### 3.4 Capability API вместо прямой инфраструктуры

Dynamic UI не получает generic `sql.query`, `redis.command`, `kafka.produce`, `mongodb.find`, `opensearch.request` или `kubernetes.exec`.

Вместо этого используются typed capabilities:

```text
projects.read
files.read
files.create
agent.sessions.read
agent.runs.read
agent.runs.start
agent.approvals.decide
equipment.requests.read
equipment.offers.search
equipment.offers.select
equipment.tenders.attach
search.catalog.query
analytics.metrics.read
```

Infrastructure adapters могут использовать PostgreSQL/OpenSearch/Redis/Kafka/Mongo внутри backend, но их запросы:

- заранее зарегистрированы и типизированы;
- ограничены tenant/project scope;
- имеют server-side max rows/bytes/cost;
- используют prepared query или approved query plan;
- не принимают произвольный backend connection string;
- логируются с `tenant_id`, `plugin_id`, `user_id`, `run_id`, `trace_id`;
- могут быть запрещены policy для конкретного tenant/cabinet.

Если в будущем потребуется пользовательский аналитический query builder, он компилируется в ограниченный AST/DSL с allowlist полей и операторов, quota estimation и read-only transaction. Это не общий SQL endpoint.

### 3.5 ACL и quota enforcement

Каждый capability call обязан нести:

```text
plugin_id + plugin_version
user_id
 tenant_id / cabinet_id / project_id
 session_id / run_id, если применимо
 capability_id + capability_version
 resource_ids
 idempotency_key
 trace_id
```

Проверки выполняются в порядке:

1. подлинность plugin/session context;
2. plugin version и signature/registry status;
3. tenant/cabinet/project membership;
4. user role and resource ACL;
5. capability grant;
6. input schema and resource ownership;
7. quota/rate/concurrency/response limits;
8. domain policy and approval requirement;
9. audit event;
10. adapter execution.

ACL никогда не проверяется только на загрузке страницы. Запросы dynamic UI должны быть безопасны при прямом повторе, replay, подмене route и ручном вызове gateway.

### 3.6 Tokens и browser isolation

- Shell auth cookie/session не передаётся plugin напрямую.
- Plugin получает короткоживущий audience-bound capability token или broker handle.
- Token ограничен `plugin_id`, version, tenant scope, expiry и capability set.
- Refresh выполняет shell/gateway, а не plugin.
- CSP: без `unsafe-eval`, без произвольных third-party origins, с ограниченным `connect-src`.
- iframe: `sandbox` без same-origin с shell; отдельный origin/site для tenant plugins.
- Нельзя размещать tenant bundle на том же origin, что и privileged app.
- Все сообщения `postMessage` проверяют origin, protocol version, request id и payload schema.

### 3.7 Code plugin supply chain

Для code plugin нужны:

- TypeScript SDK с generated capability types;
- isolated build worker;
- dependency allowlist/lockfile и license scan;
- static checks и bundle size limits;
- malware/secrets scan;
- signed immutable artifact digest;
- draft/published/disabled lifecycle;
- tenant admin approval;
- rollback на предыдущую версию;
- kill switch и automatic disable при policy violation;
- audit trail публикаций и grants.

Нельзя разрешать arbitrary npm install в runtime или грузить непроверенный remote script.

### 3.8 Backend/domain boundary

Dynamic frontend может давать пользователю возможность создавать собственные screens, но не должен создавать новые privileged backend semantics только через UI bundle. Для новой бизнес-операции нужен:

```text
capability definition
  -> backend policy/adapter
  -> typed API contract
  -> ACL/quota definition
  -> audit and tests
  -> UI plugin
```

Declarative custom entities допустимы в tenant-owned namespace, если они используют общий typed record service, RLS, quota и schema validation. Они не получают доступа к системным таблицам и не могут менять platform/runtime/equipment source-of-truth без отдельной capability.

### 3.9 Dynamic UI и module packages

Итоговая plugin taxonomy:

| Тип | Что меняет | Риск | Режим |
|---|---|---:|---|
| `view-config` | поля, layout, filters, labels | низкий | default |
| `declarative-app` | screens/forms/queries из typed schema | средний | tenant self-service |
| `code-plugin` | произвольная UI логика через SDK | высокий | isolated origin + approval |
| `backend-capability` | новые операции и domain policy | очень высокий | signed platform/partner package |
| `pod-tool-package` | MCP/tool runtime | очень высокий | pre-approved image/package + gateway ACL |

Это сохраняет исходную идею динамического фронтенда, но не смешивает UI extensibility с privileged infrastructure access.

## 4. Non-goals and explicit prohibitions

- Не сохранять Flutter как второй production UI после migration cutover.
- Не строить новый frontend поверх current DSL interpreter.
- Не создавать универсальный `table_slug + body` API для новых доменов.
- Не выполнять tenant code в privileged React shell.
- Не давать dynamic UI прямой доступ к SQL, Redis, Kafka, MongoDB, OpenSearch, MinIO или Kubernetes.
- Не выдавать plugin raw platform tokens, service credentials или unrestricted network.
- Не использовать remote module federation без отдельного origin, signature, version pinning и rollback.
- Не использовать Commerce DB как Agentscale runtime dependency.
- Не использовать OpenSearch как source of truth.
- Не давать Pod direct DB/Redis/MinIO/OpenSearch/S4B access.
- Не добавлять `:latest` в reproducible preview/runtime revisions.
- Не делать big-bang equipment rewrite без import snapshots, parity fixtures и cutover checkpoint.

## 5. Success criteria

Architecture считается пригодной к реализации, когда:

- каждый screen и command имеют typed API contract;
- каждый run воспроизводим по profile/capability/runtime revision;
- materialization даёт inspectable plan, digest и provenance;
- prompt/MCP/files имеют единый resource lifecycle;
- equipment offer имеет source, observed_at, freshness и immutable snapshot provenance;
- agent не может записать authoritative offer price;
- webhook/automation повторяются без duplicate side effects;
- domain package не требует правок core executor для обычного расширения;
- preview может запускаться с immutable images и изолированным state.
