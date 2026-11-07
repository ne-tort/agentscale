# Plugin Architecture — Полный дизайн (v2)

**Дата:** 2026-11-08 (пересмотр v1 от 2026-11-07)  
**Статус:** целевая модель, подлежит утверждению перед реализацией.  
**Принцип:** в конечном состоянии declarative-app удалён полностью («всё под нож»).  
**Метод:** strangler с фича-флагом, а не big bang — см. §8.

> v1 был отвергнут на ревью: он изобретал заново инфраструктуру, которая в репозитории
> уже работает, противоречил трём принятым ADR и содержал неработающие механизмы
> безопасности (RLS без FORCE, `CONNECTION LIMIT` как «размер БД», Knative без CRD).
> v2 построен на as-built. Полный разбор причин — в §12.

---

## 0. Цели

1. Плагин — **полноценное приложение**: свой frontend, свой backend, свои реальные таблицы (не JSONB-строки `module_instance_data_rows`).
2. Плагин **сам описывает и мигрирует свою схему** (Liquibase / Flyway / Alembic), но DDL исполняется **доверенной платформенной ролью**, а не ролью плагина.
3. Доступ к инфраструктуре — только через **capability gateway** по типизированным контрактам; плагин не знает, что за реализация стоит за адаптером.
4. Изоляция: tenant / module / project; регулируемый egress; единая модель фаерволла для плагинов **и** контейнеров агентов.
5. Динамическая загрузка кода (не на этапе сборки shell).
6. Внешние адреса: `/webhook/{plugin}/{company}/*`, `/pluginpage/{plugin}/{company}/*`.

### Не в v1 реализации

- WASM-песочница, отдельный PG-инстанс на компанию, Knative, cross-tenant sharing, marketplace.

---

## 1. Что уже есть и переиспользуется (as-built)

**Ключевое решение v2: не строить новый слой, а расширить существующий.**

| Потребитель | Уже работает | Где |
|---|---|---|
| Единая точка доступа, ACL, квоты, аудит, fail-closed | `CapabilityGatewayService.authorize/invoke`: identity → scope → grant → quota → adapter, `AuditEvent`, `DecisionCode.*` | `apps/api/src/prodavan/application/capability_gateway_service.py:180-255` |
| Токен, scope, revocation | `PodBridgeClaims` / `mint_pod_bridge_token`: `company_id`, `project_id`, `scopes`, `jti`, `exp`, `gen`, отзыв через `bump_pod_bridge_generation` | `application/pod_identity/bridge.py:76-245` |
| Structured DB без raw SQL | `TenantUserDbService`: БД `agentscale_userdb`, схема `p_<project>`, `_ALLOWED_TYPES`, `_IDENT_RE`, create/select/insert/drop/purge | `application/tenant_infra/userdb_service.py:1-172`, `infra/k3s/overlays/dev/userdb-init.yaml:42-48` |
| Квоты | `CompanyTenantInfraQuota`, `enforce_ops_rate` (ops/min, 60s window), `quota_exceeded` → 429 | `application/tenant_infra/quota.py:35-116` |
| Cache / key isolation | `rewrite_cache_key` → `tenant:{company}:proj:{project}:{key}`, denylist `prodavan:`/`celery`/`tenant:`, обязательный TTL | `application/tenant_infra/keys.py:9-56` |
| Objects | `TenantObjectsService`: один бакет `agentscale`, теги `tenant_infra` + `project:{id}`, API-mediated (presigned намеренно отвергнуты) | `application/tenant_infra/objects_service.py:1-70`, `docs/target/12-layer-docs/tenant-infra-gateway.md:30-32` |
| Docs / Events / Modules / Actions | `TenantDocsService`, `TenantEventsService`, `prodavan-modules` MCP, `pod_modules.py` | `application/tenant_infra/`, `api/v1/pod_modules.py` |
| Транспорт | pod-gateway `:8001` (`prodavan.main_pod:app`), Bridge JWT only, NP egress только DNS + 80/443 + TCP 8001 | `docs/target/12-layer-docs/tenant-infra-gateway.md:10-20` |
| Purge при uninstall | `purge_project_tenant_infra` (cache/docs/userdb/events/objects + equipment OS indexes) | `application/tenant_infra/lifecycle.py:12-94` |
| Pause / resume / warm pool | `SandboxClaim.spec.operatingMode: Running \| Suspended`, `PodRuntimePort.pause/resume`, `SandboxWarmPool` (2 прогретых), `ensure_running_for_project` = lazy start | `application/pod_service/adapters/agent_sandbox/runtime.py:16-22,180-200,292-300,336-374`, `command.py:366-389,600-626` |
| Маршрутизация | `networking.k8s.io/v1 Ingress` + `ingressClassName: traefik` + аннотация `router.priority` (110 для `/api`, 100 для `/`, 50 prod wildcard) | `infra/k3s/overlays/dev/ingress-https-{api,web}.yaml`, `infra/ops/tests/test_ingress_priority.py` |

**Следствия:** новый сервис `capability-adapters:9000`, новый JWT-формат, PgBouncer, `CREATE DATABASE` на компанию, bucket-per-company, Knative — **не строятся**. Всё это уже либо есть в другой форме, либо конфликтует с инфраструктурой.

---

## 2. Модель данных

### 2.1 Одна БД, схема на компанию и модуль

```
PostgreSQL (один Deployment, replicas: 1, PVC 5Gi — infra/k3s/overlays/dev/postgres.yaml)
├── prodavan            — платформенная БД, Alembic, SoT приложения. Плагину НЕ доступна.
└── agentscale_userdb   — уже создаётся one-shot Job'ом (userdb-init.yaml)
    └── tenant_<company_id>_module_<module_id>   — схема плагина
        ├── реальные таблицы плагина (типизированные колонки, индексы, constraints)
        ├── plugin_migrations / DATABASECHANGELOG  — журнал миграций
        └── данные: company-wide, кабинет/project-скоуп через колонки cabinet_id/project_id
```

**Почему не `CREATE DATABASE tenant_<company_id>`:**

- `CREATE DATABASE` не имеет `max_size`; `CONNECTION LIMIT` ограничивает **сессии**, а не размер (PostgreSQL 17 `sql-createdatabase`).
- N БД = N наборов каталогов, N целей миграций, N объектов бэкапа. Автоматических бэкапов в проекте нет вообще (`docs/07-infrastructure/topology.md:208-220`), поэтому «архивация переименованием» из v1 не является бэкапом.
- PgBouncer в `infra/` отсутствует; «отдельный pool на tenant DB» при `max_connections=100` и 1Gi RAM быстро упирается в потолок.
- Одна линейная цепочка Alembic (`docs/07-infrastructure/alembic.md:34-72`) остаётся источником истины схемы.

**Отдельный PG-инстанс на компанию** — не v1: single-node k3s с `ResourceQuota pods: 45`, `limits.memory: 26Gi` и тремя OOM-инцидентами в истории (`runbook.md:484-554`). Оставляем как enterprise-tier с выделенной нодой.

### 2.2 Реальные таблицы вместо JSONB

Плагин объявляет структуру структурированным манифестом (как уже делает `TenantUserDbService._ALLOWED_TYPES`):

```json
POST /gateway/plugins/{plugin_id}/schema
{
  "tables": [
    {
      "name": "equipment_offers",
      "columns": [
        {"name": "id", "type": "uuid", "primary_key": true},
        {"name": "cabinet_id", "type": "uuid", "index": true},
        {"name": "project_id", "type": "uuid", "index": true},
        {"name": "price", "type": "numeric(14,2)"},
        {"name": "currency", "type": "text"}
      ],
      "constraints": [{"unique": ["cabinet_id", "part_number"]}]
    }
  ]
}
```

Gateway создаёт реальные таблицы **под своей доверенной ролью**. Плагин получает `USAGE` на схему и `SELECT/INSERT/UPDATE/DELETE` на таблицы — но **не `CREATE`** и не владение.

Это и есть ответ на «данные должны быть реальными, не эфемерными в jsonb»: настоящие колонки, типы, индексы, constraints, `EXPLAIN`, `JOIN` — всё доступно. Эфемерность исчезает, DSN плагину при этом не выдаётся.

### 2.3 Два варианта доступа к БД — явный выбор

| | **A. Gateway (рекомендуется, v1)** | **B. Прямой DSN (enterprise, позже)** |
|---|---|---|
| Реальные таблицы | ✅ | ✅ |
| Произвольный SQL / JOIN / CTE | typed subset + параметризованные запросы | ✅ полный |
| DDL | доверенный адаптер по ревьюенному манифесту | плагин, но под `NOCREATEDB NOCREATEROLE` |
| Граница безопасности | gateway: identity → scope → grant → quota → audit | `GRANT` + отдельная схема + NetworkPolicy + `pgaudit` |
| Утечка в чужую БД | исключена конструктивно | требует отдельного инстанса / жёсткого лимита компаний |
| Риск | сужение выразительности запросов | RLS-обход владельцем, подмена GUC, N БД, нет бэкапов |
| Соответствие ADR-003 | ✅ | ❌ требует явного supersede |

**Рекомендация: A.** Пользовательский запрос на «прямой доступ» удовлетворяется по смыслу (реальные таблицы, миграции, SQL), а не буквенно (DSN). Вариант B оставлен как осознанный trade-off: если он будет выбран, до реализации обязательны отдельный PG-инстанс, `pgaudit`, бэкапы, `FORCE ROW LEVEL SECURITY` + `NOBYPASSRLS` + платформенный owner таблиц, и отдельный ADR, отменяющий ADR-003.

### 2.4 Почему RLS не является границей

В v1 RLS заявлялся как «обязательная защита всех таблиц плагина». Это не работает:

- `ENABLE ROW LEVEL SECURITY` **не применяется к владельцу таблицы** (PG 17 `ddl-rowsecurity`). Плагин по v1 сам создавал таблицы → становился владельцем → RLS на его же таблицах для него не действовал.
- Владелец может `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` / `DROP POLICY`.
- `current_setting('app.tenant_id')` — пользовательский GUC (USERSET), плагин с DSN может пере-set'ить его сам. `REVOKE SET ON PARAMETER` существует в PG 16/17, но осмысленна только для superuser-only параметров; `app.*` всегда settable.
- `ALTER ROLE ... SET statement_timeout` — это **session default при логине**, а не rate limit; обычный `SET` в сессии его переопределяет.
- При БД-на-компанию RLS по `tenant_id` защищает от… той же компании, то есть бессмысленна.

**Решение v2:** граница = отдельная схема + `GRANT` без `CREATE` + gateway + NetworkPolicy. RLS **не используется** как межтенантовый барьер. Если он нужен для дополнительной защиты внутри схемы (по `cabinet_id`/`project_id`), то только в связке: `FORCE ROW LEVEL SECURITY` + `WITH CHECK` + таблицы принадлежат платформенной роли + `NOBYPASSRLS` + значение подставляет адаптер в `SET LOCAL` из доверенного JWT.

---

## 3. Миграции схемы (Liquibase и аналоги)

### 3.1 Принцип

Плагин **поставляет** changelog, платформа **исполняет** его. Это снимает главный риск: произвольный DDL из тела HTTP-запроса без ревью, без `alembic check`, при N×M состояниях схемы.

```
Артефакт плагина
└── migrations/
    ├── liquibase/changelog.xml      ← рекомендуемый
    ├── alembic/versions/            ← для Python-плагинов
    └── flyway/V1__init.sql          ← альтернатива
```

```json
POST /gateway/plugins/{plugin_id}/migrations/apply
{
  "version": "1.2.0",
  "engine": "liquibase",
  "changelog_ref": "artifact://plugin-mod_equipment@1.2.0/migrations/liquibase/changelog.xml",
  "contexts": ["prod"],
  "labels": ["v1.2"],
  "checksum": "sha256:..."
}
```

### 3.2 Исполнение

Liquibase запускается **изолированным k8s Job**:

- отдельная доверенная роль (владелец схемы), `NOSUPERUSER NOCREATEDB NOCREATEROLE`
- `readOnlyRootFilesystem: true`, `runAsNonRoot`, `capabilities.drop: [ALL]`, `resources.limits` 500m/512Mi
- changelog монтируется read-only из артефакта (проверен digest + подпись Ed25519)
- `LIQUIBASE_ALLOW_EXECUTE_COMMAND=false` (запрет `<executeCommand>`)
- egress по NetworkPolicy: только PostgreSQL и gateway, **без интернета**
- результат + checksum пишутся в `DATABASECHANGELOG` и в audit `plugin_migration_applied`

**Валидация перед запуском**: статический парсер отклоняет `DROP DATABASE`, `DROP SCHEMA` вне своей, `CREATE ROLE`, `ALTER ROLE ... SUPERUSER`, `GRANT ... ON ALL TABLES IN SCHEMA public`, `COPY ... TO PROGRAM`, `pg_read_file`, `lo_export`, `dblink`/`postgres_fdw`, `SET row_security = off`.

### 3.3 Дополнительные операции

| Операция | Контракт |
|---|---|
| `POST .../migrations/plan` | dry-run: `updateSQL`, возвращает планируемые changesets без применения |
| `POST .../migrations/rollback` | `rollbackCount` / `rollbackTo` (только Liquibase/Flyway с явным DOWN) |
| Checksum mismatch | `422` + `plugin_migration_checksum_mismatch`, применение блокируется |

**Идемпотентность:** повторный `apply` того же `version`+`checksum` → `200` без изменений. Версия плагина в имени схемы **не** используется (иначе при обновлении данные остаются в старой схеме); версия живёт в журнале миграций.

### 3.4 Схема называется `tenant_<company>_module_<module>` без версии

Изменение схемы — это миграция внутри неё, а не новая схема. Старые данные не теряются, `pluginpage` не меняет URL при обновлении плагина.

---

## 4. Capability Layer

### 4.1 Не новый сервис, а расширение `:8001`

Запросы плагина идут в тот же pod-gateway, что и запросы агентов: Bridge JWT → `CapabilityGatewayService` → typed adapter. Новые capability добавляются в `DECLARATIVE_CAPABILITIES`/`CapabilityGrant`, а не в отдельный HTTP-сервис.

### 4.2 Capability Token

Переиспользуется `PodBridgeClaims` (не новый JWT-формат):

```json
{
  "iss": "prodavan",
  "sub": "plugin:mod_equipment:co_123:inst_abc",
  "company_id": "co_123",
  "cabinet_id": "cab_456",
  "project_id": "proj_789",
  "scopes": [
    "module:mod_equipment:rows:read",
    "module:mod_equipment:rows:write",
    "module:mod_equipment:migrations:apply",
    "objects:read", "objects:write",
    "network:webhooks-only"
  ],
  "quota_ref": "co_123/plan_pro",
  "egress": {"mode": "webhooks-only", "allowed_hosts": ["hooks.slack.com"], "allowed_paths": ["/services/*"]},
  "jti": "...", "gen": 3,
  "exp": 1794163200          // иллюстрация: короткий TTL, реальное значение = now + 300 с при выдаче/ wake-up
}
```

Отзыв — `bump_pod_bridge_generation` (уже реализован), а не «ждать истечения токена». Токен передаётся только в `Authorization`, **никогда в URL query**. Ротация при wake-up не нужна: `gen` проверяется на каждом запросе.

### 4.3 Контракты адаптеров (типизированные, без generic raw SQL)

ADR-002-capability-gateway прямо запрещает «generic row/action dispatch как публичный контракт». Поэтому вместо `POST /adapters/db/query {"sql": "SELECT ..."}`:

| Адаптер | Контракт | Примечание |
|---|---|---|
| `db.schema` | структурированный манифест таблиц/колонок/индексов | DDL только от платформенной роли |
| `db.query` | `{table, select[], where{}, order, limit}` + параметры | SQL собирается адаптером; схема+таблица проверяются по grant |
| `db.mutate` | `{table, op: insert\|update\|delete, values{}, where{}}` | `WITH CHECK`-эквивалент на стороне адаптера |
| `db.migrations` | см. §3 | changelog, не SQL из тела |
| `cache` | `get/set/delete/incr/publish` с key-rewrite | `tenant:{company}:proj:{project}:plugin:{module}:*` |
| `objects` | `put/get/list/delete` через API-mediated | без presigned URL (as-built решение) |
| `docs` | Mongo namespace `tenant_infra` | `TenantDocsService` |
| `events` | `publish/subscribe` | `TenantEventsService` |
| `pods` | `invoke` typed action | плагин не имеет k8s API |
| `mcp` | `register` typed tools | MCP = адаптер к gateway |
| `secrets` | `read` по ссылке | плагин не хранит plaintext |
| `network` | egress-прокси | см. §6 |

Квоты берутся из `CompanyTenantInfraQuota` (`enforce_ops_rate`, max keys, max bytes), а не из выдуманных полей токена.

### 4.4 Единый реестр идентификаторов

Имена capability, квот и audit-событий фиксируются **в одном месте** — в этом документе; PRODUCT.md и ADR ссылаются сюда. Расхождения словарей из v1 устранены.

| Группа | Идентификатор | Enforcement |
|---|---|---|
| capability | `db.schema`, `db.query`, `db.mutate`, `db.migrations`, `cache`, `objects`, `docs`, `events`, `jobs`, `pods`, `mcp`, `secrets`, `network` | `CapabilityGrant.operations` × `resources` × `domains` |
| quota | `ops_per_minute`, `max_rows`, `max_bytes`, `max_keys`, `max_object_bytes`, `max_egress_bytes` | `CompanyTenantInfraQuota` в gateway; превышение → `429` + `QUOTA_EXCEEDED` |
| event | `plugin_request`, `plugin_db_query`, `plugin_migration_applied`, `plugin_object_access`, `plugin_egress_request`, `plugin_webhook_received`, `plugin_page_opened`, `plugin_wake_up`, `plugin_task_submitted` | append-only sink, см. §4.5 |

**Точка enforcement — gateway, до адаптера.** `pg_database_size()` и `statement_timeout` остаются observability/защитой от зависших запросов, но **не** считаются квотами: alert ≠ hard limit. Таймаут задаётся сервером, не полем `timeout_ms` из тела запроса.

### 4.5 Аудит

Каждый запрос → `AuditEvent`: `request_id`, `run_id`/`correlation_id`, `company_id`, `cabinet_id`, `project_id`, `subject_id`, `capability`, `operation`, `resource`, `decision`, `code`, `grant_id`, `reason`.

Хранение — append-only:

- **Никогда не пишутся** raw SQL, параметры с данными, тела payload, токены, секреты, PII. Только канонические digest-ы (`sql_digest`, `payload_digest`, `artifact_digest`) по образцу `docs/08-migration/audit-append-shape-decision.md:13-19`.
- Связка «решение об авторизации» и «факт исполнения» — разные события; успех одного не подразумевает успех другого.
- Append failure для позитивной мутации = deny/system failure, а не allow.
- Retention и tamper-evidence задаются политикой; `plugin_egress_request` и `agent_egress_request` имеют **одинаковую** схему.

---

## 5. Артефакт и загрузка кода

```
plugin-mod_equipment-1.2.0.tar.gz        (digest sha256, подпись Ed25519)
├── manifest.json
│   {
│     "plugin_id": "mod_equipment", "version": "1.2.0",
│     "frontend": {"entry": "index.html"},
│     "backend": {"image": "ghcr.io/.../plugin-mod-equipment:1.2.0", "port": 8080, "health": "/health"},
│     "capabilities": ["db.schema","db.query","db.mutate","db.migrations","cache","objects","pods","mcp","secrets","network"],
│     "migrations": {"engine": "liquibase", "path": "migrations/liquibase/changelog.xml"},
│     "egress": {"mode": "webhooks-only", "allowed_hosts": [], "allowed_paths": []},
│     "public_pages": [],          // явный список страниц без авторизации
│     "webhooks": ["/webhook/external-system"]
│   }
├── frontend/  dist/
├── migrations/liquibase/
└── metadata/
```

**Установка (идемпотентная):**
1. Реестр артефактов: скачать по digest, проверить подпись.
2. Frontend → объектное хранилище с tenant-тегами (не отдельный bucket).
3. Backend → контейнер по образу из манифеста; env получает **только** `GATEWAY_URL`, `PLUGIN_ID`, `BRIDGE_TOKEN` (из k8s Secret). **`TENANT_DB_DSN` не выдаётся.**
4. Миграции → Job по §3.2.
5. Grant/bind → запись в `CapabilityGrant` + `PodBridgeClaims`.
6. Регистрация маршрутов в Plugin Gateway и egress-правил.

### 5.1 Frontend: отдельный origin, без `allow-same-origin`

v1 использовал `sandbox="allow-scripts allow-same-origin"` на том же `agentscale.dev` — сочетание позволяет скрипту **снять собственный sandbox**. Исправлено:

```html
<iframe
  src="https://plugin-origin.agentscale.dev/tenant-co_123/mod_equipment/index.html"
  sandbox="allow-scripts allow-forms"          <!-- БЕЗ allow-same-origin -->
  referrerpolicy="no-referrer"
  credentialless
></iframe>
```

- отдельный origin (не `agentscale.dev`), opaque origin, собственный CSP
- общение с shell только через `postMessage` с проверкой `event.origin`; протокол — `plugin-rpc/1` handshake (`plugin.ready` с `plugin_id`/`version`/`digest`), как требует `docs/08-migration/dynamic-ui-implementation-contract.md:216-239`
- capability token **не** попадает во frontend — только в backend, коротким TTL через handshake; **никогда в URL query** (утечка через Referer, историю и access-логи)
- публичные страницы объявляются явно в `manifest.public_pages`. По умолчанию Gateway требует сессию и проверяет `employee ∈ company` + grant; публичная страница получает урезанный anon-scope с отдельным rate limit. Вариант «прямой redirect без iframe» исключён: он уводит страницу в общий origin shell.

### 5.2 Supply chain артефакта

Проблема v1: `artifact_digest` лежал **внутри** того же manifest, который подписывался, — самореференция. Исправлено:

```
подписывается ≠ содержимое манифеста
manifest.json          (каноническая форма, без digest/подписи)
    ↓ sha256 → artifact_digest (в реестре, не в манифесте)
    ↓ Ed25519 sign → signature (в реестре)
```

- **Реестр ключей**: trust root платформы, ротация и отзыв ключей, список доверенных издателей.
- **Проверка до деплоя**: digest → signature → compatibility (`plugin_id`/`version`/capabilities против grant) → сканирование образа → CSP-политика для frontend.
- **Откат**: предыдущая версия остаётся в реестре; `quarantine` при несовпадении digest; `rollback` фиксируется событием `plugin_artifact_quarantined`.
- Статика версионируется (`.../plugins/<module_id>/<version>/`), чтобы обновление не перезаписывало файлы работающей версии.

### 5.3 Lifecycle: grant / revoke / uninstall

| Операция | Что делает |
|---|---|
| `POST /gateway/plugins/{id}/grant` | создаёт `CapabilityGrant`, мнит `PodBridgeClaims`, создаёт схему/роль, регистрирует маршруты и egress |
| `POST /gateway/plugins/{id}/revoke` | `bump_pod_bridge_generation` → мгновенный отзыв всех токенов, снятие маршрутов, перевод backend в `Suspended` |
| `POST /gateway/plugins/{id}/uninstall` | `purge_project_tenant_infra` (cache/docs/userdb/events/objects + equipment OS indexes), drop схемы, удаление артефакта. Требует snapshot |
| `POST /admin/modules/{id}/migrate-to-plugin` | идемпотентный перенос данных: `dry_run` → сверка контрольных сумм → копирование → журнал. Односторонний, поэтому snapshot обязателен **до** запуска |
| `POST /gateway/plugins/{id}/wake` | см. §7.6; выдаёт **новый короткоживущий** lease, проверяет отзыв перед первым запросом |

Долгоживущих креденшелов в env нет: только `GATEWAY_URL`, `PLUGIN_ID`, короткий bridge lease; `secret_ref` вместо plaintext (ADR-003).

---

## 6. Сеть и фаерволл (единая модель для плагинов и агентов)

### 6.1 NetworkPolicy

Исправления относительно v1: `namespaceSelector` по `kubernetes.io/metadata.name` (лейбла `name` в `namespace.yaml` нет), **убран** egress на `postgresql:5432` для плагина (комментарий в YAML не является ограничением; as-built явно «Platform Postgres | Never — no DSN»).

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: plugin-egress
  namespace: agentscale-dev-sandboxes
spec:
  podSelector:
    matchLabels: {app: plugin}
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: agentscale-dev}
          podSelector:
            matchLabels: {app: plugin-gateway}
      ports: [{protocol: TCP, port: 8080}]
  egress:
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: agentscale-dev}
          podSelector:
            matchLabels: {app: pod-gateway}
      ports: [{protocol: TCP, port: 8001}]
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: agentscale-dev}
          podSelector:
            matchLabels: {app: egress-proxy}
      ports: [{protocol: TCP, port: 8080}]
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: kube-system}
          podSelector:
            matchLabels: {k8s-app: kube-dns}
      ports: [{protocol: UDP, port: 53}]
```

Прямой доступ к PostgreSQL разрешён **только** подам gateway и Liquibase Job, не плагину.

### 6.2 Egress Proxy

| Режим | Поведение |
|---|---|
| `none` | только gateway + DNS |
| `webhooks-only` | разрешённые `allowed_hosts[]` × `allowed_paths[]` из токена |
| `full` | любой egress, но с обязательным approval, логированием и квотой байт |

**Исправление v1:** Envoy-кластер не статический (`webhooks.example.com` захардкожен), а строится контроллером из `allowed_hosts` capability token; при отсутствии совпадения → `403` + `direct_response`. DNS-резолв через отдельный egress-прокси, чтобы плагин не мог обойти правила прямым IP.

**Аудит:** `plugin_egress_request` / `agent_egress_request` — единая схема (host, path, method, status, bytes, decision, tenant, subject). Та же модель применяется к контейнерам агентов: «регулируемая изоляция контейнеров» достигается одним контроллером и одним набором правил, а не двумя реализациями.

### 6.3 Подмена заголовков

Plugin Gateway **перезаписывает** `X-Plugin-ID`, `X-Company-ID`, `X-Project-ID`, `X-Plugin-Capability-Token`, а не пропускает их из клиентского запроса (v1 делал `**request.headers` — внешний клиент мог выдать себя за другой плагин). Входящие `Authorization` и `Cookie` отбрасываются.

---

## 7. Внешняя маршрутизация, backend и засыпание

### 7.1 Пути

```
https://agentscale.dev/webhook/{plugin}/{company}/*
https://agentscale.dev/pluginpage/{plugin}/{company}/        → frontend (static)
https://agentscale.dev/pluginpage/{plugin}/{company}/api/*   → backend
```

Path-based выбран сознательно: поддомены `{plugin}.appname.com` требуют wildcard-сертификата и интеграции с DNS-провайдером (reg.ru), а в кластере TLS = self-signed default TLSStore, cert-manager не используется (`docs/07-infrastructure/k3s-services.md:117-121`). Path-based не требует ничего, кроме одного Ingress.

**Ingress (исправленный под реальный Traefik-контракт):**

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: plugins-gateway
  annotations:
    argocd.argoproj.io/sync-wave: "25"
    traefik.ingress.kubernetes.io/router.entrypoints: websecure
    traefik.ingress.kubernetes.io/router.tls: "true"
    traefik.ingress.kubernetes.io/router.priority: "120"   # > 100 у SPA, > 110 у /api
spec:
  ingressClassName: traefik
  tls:
    - hosts: [agentscale.dev, localhost]
  rules:
    - host: agentscale.dev
      http:
        paths:
          - path: /webhook
            pathType: Prefix
            backend: {service: {name: plugin-gateway, port: {number: 8080}}}
          - path: /pluginpage
            pathType: Prefix
            backend: {service: {name: plugin-gateway, port: {number: 8080}}}
```

Обязательно: добавить файл в `overlays/dev/kustomization.yaml` и тест по образцу `infra/ops/tests/test_ingress_priority.py`. Без `router.priority` повторится инцидент «login POST → index.html» (тот самый, который уже чинили в #331).

### 7.2 Plugin Gateway (всегда running)

- проверяет grant/bind и активность плагина;
- **требует сессию** для `/pluginpage/*` (кроме явно объявленных `public_pages`) и проверяет `employee ∈ company`; для `/webhook/*` проверяет подпись (см. §7.5);
- строит исходящие заголовки **allowlist'ом**: `X-Plugin-ID`, `X-Company-ID`, `X-Project-ID`, `X-Plugin-Capability-Token` перезаписываются доверенными значениями; все входящие `X-Plugin-*`, `Cookie`, `X-Forwarded-*`, hop-by-hop — отбрасываются (в v1 клиентские заголовки распаковывались после доверенных и перезаписывали их);
- будит backend (см. §7.6) и выдаёт новый короткий lease;
- проксирует с серверным таймаутом, rate limit и retry только на идемпотентные методы;
- backend **обязан** проверить JWT, а не доверять Gateway «по умолчанию»;
- логирует `plugin_webhook_received` / `plugin_page_opened`.

### 7.3 Backend плагина: полный набор capability

| Возможность | Регистрация | Триггер | Endpoint плагина |
|---|---|---|---|
| Входящие webhook'ы | `manifest.webhooks` | внешний HTTP | `POST /webhook/*` |
| Исходящие webhook'ы | `egress.allowed_hosts` | код плагина | egress-proxy |
| Публичные страницы | `manifest.public_pages` | GET `/pluginpage/...` | static + `/api/*` |
| Scheduled tasks | `POST /gateway/cron/register` | Celery beat → outbox | `POST /cron/*` |
| Event handlers | `POST /gateway/events/subscribe` | Kafka/RabbitMQ | `POST /events/*` (DLQ, 3 retry) |
| Background workers | `POST /gateway/tasks/submit` | k8s Job | `POST /tasks/*` |
| Миграции | `POST /gateway/migrations/apply` | Liquibase Job | — |

Backend — отдельный контейнер (или sidecar), не имеет k8s API, не имеет DSN, общается только с gateway и egress-proxy.

**Scheduler / events / jobs — не ad-hoc cron.**

- Расписания и события проходят через **уже существующий durable project-trigger outbox** (`PRODUCT.md:71`, `docs/migration-architecture.md:250-254`): claim через `FOR UPDATE SKIP LOCKED` + lease, retry, idempotency key, `run_id`/`correlation_id`. Celery beat/enqueue, а не «CronJob будит Knative» (как в v1); см. `docs/target/13-platform-infra/stack.md:52`.
- Типизированные события (`project.sync.completed`, `agent.turn.completed`, …) идут через `events` capability с DLQ и 3 retry.
- Долгая задача возвращает `job_id` + статус; результат читается через `jobs.get`. Пока есть активный job, lifecycle-контроллер **не усыпляет** backend.

### 7.5 Webhook-контракт

Переиспользует as-built `docs/external-api.md:34-51`, а не изобретает новый:

| Аспект | Контракт |
|---|---|
| Подписка | `POST /api/v1/external/webhooks {url, events, project_id?}` возвращает `secret` **один раз**; HMAC в secret store, не в БД |
| Входящая подпись | `X-Prodavan-Signature: sha256=<hmac>` + `X-Prodavan-Event`, `X-Prodavan-Delivery`; `sha256 = HMAC(secret, raw_body)` |
| Идемпотентность | `X-Prodavan-Delivery` как idempotency key; повтор → `200` без повторной обработки; защита от replay по времени |
| Доставка | outbox `webhook_deliveries` + Celery beat каждые 15 с; ретраи 1м/5м/30м/2ч (5 попыток) → `exhausted`; авто-отключение подписки после 10 ошибок подряд; claim `FOR UPDATE SKIP LOCKED` |
| Операторская видимость | `GET /api/v1/external/webhook-deliveries` + ручной `retry` только для `exhausted` |
| Ротация | секрет ротируется независимо; старый ключ живёт до исчерпания in-flight доставок |
| Rate limit | на Ingress + в Gateway, per `(plugin, company)` |

Исходящие webhook'ы плагина идут через тот же egress-контроллер с `allowed_hosts`/`allowed_paths` и логированием.

### 7.6 Засыпание: переиспользуем pause/resume агентов

**Knative удалён.** Причины: CRD `serving.knative.dev` в `infra/` нет, Argo `AppProject` имеет жёсткий whitelist kinds (`infra/argocd/apps/00-project.yaml:36-88`), HPA в кластере нет ни одного, `kubectl scale` запрещён AGENTS.md, а холодный старт 100–500 мс из v1 нереалистичен на этом хосте (pull образов — до 18 минут по `runbook.md:223-227`).

Вместо этого — **тот же механизм, что уже работает у агентов**:

| Аспект | Реализация |
|---|---|
| Состояние | `SandboxClaim.spec.operatingMode: Running \| Suspended` (уже в CRD) |
| Пауза | `PodRuntimePort.pause` → Pod удаляется, volume/Sandbox сохраняются |
| Пробуждение | `ensure_running_for_project` = lazy start по первому запросу |
| Idle-таймер | `pod_metrics_sampler_worker` + TTL/`shutdownTime` |
| Холодный старт | `SandboxWarmPool` (2 прогретых Sandbox) — готовый ответ, которого не было в v1 |
| Gateway | `POST /gateway/plugins/wake` → `202` + `estimated_wake_up_ms` (секунды, не 500 мс) |

Wake-up триггеры: HTTP-запрос (iframe, webhook, pluginpage), Cron перед срабатыванием, Event до доставки, явный `wake`. Плагин и агент используют **один** lifecycle-контроллер — это и есть «универсальная система».

**GitOps-совместимость:** desired-состояние хранится в поле CRD/БД, а не в `spec.replicas` Deployment, поэтому Argo `selfHeal` не будет бесконечно возвращать replicas.

---

## 8. Путь миграции: цель «под нож», метод — strangler

Разрешаем противоречие v1: **«всё под нож» — это про конечное состояние, а не про способ перехода.** В финале declarative-app (`module_meta_documents`, generic runtime, `module_instance_data_rows`, `ModuleRuntime.tsx`) удалён полностью. Но идём к нему параллельным прогоном, потому что:

- `mod_equipment` — ~13k строк: 18 Python-файлов (9 678 строк) + 16 TS/TSX (3 485 строк), отдельная страница `/equipment`, Celery beat/sweep, MCP-сервер, OpenSearch-индексы (`ADR-005-equipment-bounded-context` требует сохранения provenance и стабильных ID).
- Работа идёт прямо в `main` с auto-merge и единственным dev-контуром; бэкапов нет (`topology.md:208-220`); DOWN-миграции не поддерживаются. Big bang = неоткатываемый простой и потеря данных.

**Этапы:**

1. **Слой capability** — расширить `:8001`, `CapabilityGatewayService`, `TenantUserDbService` (новые typed контракты из §4.3), Liquibase-Job, egress-контроллер. Старый путь работает как прежде.
2. **Пилот** — plugin-версия одного bounded context'a для 1 компании за фича-флагом `plugin_enabled_modules[]`. Dual-read: старый `/equipment` и новый плагин читают одни данные; расхождения фиксируются метрикой парности.
3. **Перенос frontend** — страница плагина в iframe, backend-логика пока остаётся в API через существующие `/projects/{id}/modules/...`.
4. **Перенос backend по одному контексту** — офферы → цены → бюджет → документы → тендеры. Каждый шаг за флагом, с dual-write и сверкой.
5. **Cutover** — переключение флага, N недель наблюдения.
6. **Удаление** — убрать declarative-app, `mod_management` перевести на plugin (или оставить как единственный простой плагин), старые таблицы архивировать и удалить после сверки.

`POST /admin/modules/{id}/migrate-to-plugin` — идемпотентный, с `dry_run`, сверкой контрольных сумм и журналом; до включения обязателен snapshot (раз бэкапов нет, его надо добавить **до** этапа 2).

---

## 9. Формат работы

- Ветка: `main` (dev), без feature-веток. Микрокоммиты `git add -A && git commit && git push`, без гигиены и без переписывания истории.
- Каждый коммит → PR в main → CI Gate → auto-merge → CI Images → Argo → Verify Dev.
- Тесты пишутся как часть реализации, но **не запускаются** до явного решения (verification freeze). Исключение: инфраструктурные проверки, требующие живого кластера, выносятся в Verify Dev.
- Изоляция между субагентами — по файлам/директориям; общий коммит делается одной командой без «кто чьё закоммитил».

Подробнее: [`docs/workflow/main-branch-workflow.md`](../workflow/main-branch-workflow.md).

---

## 10. Матрица рисков

| Риск | Митигация |
|---|---|
| Произвольный DDL затирает чужие схемы | changelog исполняется доверенной ролью в изолированном Job; статическая валидация запрещённых statement; `NOCREATEDB NOCREATEROLE`; egress Job без интернета |
| Утечка между компаниями | отдельная схема + grant без `CREATE` + gateway проверяет `company_id` из JWT; RLS не является границей |
| Подмена `X-Plugin-*` внешним клиентом | Gateway строит headers allowlist'ом, отбрасывает входящие `X-Plugin-*`/`Cookie`/hop-by-hop; backend дополнительно проверяет JWT |
| Анонимный доступ к `/pluginpage/*` | обязательная сессия + `employee ∈ company` + grant; `public_pages` — явное исключение с урезанным scope |
| iframe снимает sandbox | отдельный origin, без `allow-same-origin`, restrictive CSP, `plugin-rpc/1` handshake, проверка `event.origin`; токен не в URL |
| Исчерпание соединений/памяти PG | одна БД, typed gateway, `CompanyTenantInfraQuota`, без PgBouncer и без N пулов |
| DoS через egress | `none`/`webhooks-only`/`full`, динамический allowlist из токена, квота байт, аудит |
| `/webhook/` уезжает в SPA | `router.priority: 120` + entrypoints + ingressClassName + тест приоритетов |
| Неоткатываемый cutover | strangler + фича-флаг + dual-read + snapshot до переноса данных |
| Расхождение политик | один `CapabilityGatewayService`, один `PodBridgeClaims`, один egress-контроллер |

---

## 11. Acceptance-критерии (готовность к реализации)

Дизайн считается утверждённым, когда по каждому пункту есть тест или проверка в CI/Verify Dev. Тесты пишутся вместе с кодом, но не запускаются до явного решения о начале верификации.

| # | Критерий | Проверка |
|---|---|---|
| 1 | Роль плагина не владеет таблицами и не имеет `CREATE` на схему | `\du` / `\dn+` в integration-тесте; попытка `CREATE TABLE` → `42501` |
| 2 | Changelog с `DROP DATABASE` / `COPY ... TO PROGRAM` / `CREATE ROLE` отклоняется до запуска Job | unit-тест валидатора changelog |
| 3 | Повторный `migrations/apply` того же `version+checksum` не меняет схему | integration: `DATABASECHANGELOG` без новых строк |
| 4 | Обновление плагина 1.2.0 → 1.3.0 сохраняет данные | integration: строка, созданная до апдейта, читается после |
| 5 | Запрос с подменённым `X-Company-ID` не видит чужую схему | e2e Gateway: `403` / пустой результат |
| 6 | Анонимный `/pluginpage/{plugin}/{company}/api/*` без сессии → `401` | e2e Gateway |
| 7 | Webhook без/с неверной `X-Prodavan-Signature` → `401`; повтор `X-Prodavan-Delivery` не дублирует обработку | e2e по `docs/external-api.md` |
| 8 | Egress на хост вне `allowed_hosts` → `403` + событие аудита | e2e egress-контроллера |
| 9 | `/webhook/` и `/pluginpage/` не уезжают в SPA | тест приоритетов по образцу `test_ingress_priority.py` |
| 10 | Превышение `ops_per_minute` → `429` + `QUOTA_EXCEEDED` | unit/integration gateway |
| 11 | Audit-событие не содержит raw SQL, параметров, токенов, payload | unit-тест redaction по `audit-append-shape-decision.md` |
| 12 | `revoke` мгновенно делает старый токен невалидным | integration `bump_pod_bridge_generation` |
| 13 | Wake-up после `Suspended` выдаёт новый lease, а не истёкший из Secret | integration lifecycle |
| 14 | Активный `job_id` блокирует усыпление backend | integration lifecycle |
| 15 | `migrate-to-plugin` идемпотентен; `dry_run` не пишет данные; контрольные суммы совпадают | integration миграции данных |
| 16 | Dual-read pilot: расхождение старого и нового пути фиксируется метрикой парности | метрика + алерт |

---

## 12. Открытые вопросы

1. **Выразительность `db.query`** — где граница между typed subset и необходимостью произвольного SQL для отчётов `mod_equipment`? Возможен «режим B» (§2.3) для отдельного enterprise-инстанса. **Блокирует реализацию.**
2. **Хранение артефактов** — реестр с подписью: GHCR + cosign/Ed25519 или отдельный MinIO-префикс?
3. **Публичные страницы** — какие данные допустимы без сессии; формат anon-scope.
4. **Cross-module sharing** — пока запрещено; если понадобится, только через явный grant в gateway.
5. **Бэкапы** — обязательны до этапа 2 (сейчас их нет); pg_dump-job + snapshot-политика. **Блокирует перенос данных.**
6. **Маркетплейс** — публикация/установка плагинов, отдельный документ.
7. **Billing** — привязка `quota_ref` к тарифу.

---

## 13. Что отвергнуто в v1 и почему

| Было в v1 | Почему отвергнуто |
|---|---|
| `CREATE DATABASE tenant_<company_id>` | N БД, нет `max_size`, нет бэкапов, PgBouncer отсутствует, одна цепочка Alembic |
| Прямой `TENANT_DB_DSN` плагину | противоречит ADR-003 (`no direct infra access`); RLS-обход владельцем; подмена GUC |
| RLS как граница | `ENABLE` без `FORCE` не действует на owner; owner = плагин; `app.*` GUC пере-set'ится |
| `CONNECTION LIMIT` как размер БД | это лимит сессий |
| `statement_timeout` как rate limit | это таймаут statement, переопределяется в сессии |
| `REVOKE SET ON PARAMETER` как защита | осмысленна только для superuser-only параметров |
| `capability-adapters:9000` | дублирует `:8001`, `CapabilityGatewayService`, `tenant_infra/*` |
| `POST /adapters/db/query {"sql": ...}` | запрещено ADR-002-capability-gateway (generic row dispatch) |
| Bucket-per-company + presigned URL | as-built: один бакет `agentscale`, tenant-теги, presigned намеренно отвергнуты |
| Knative scale-to-zero | нет CRD, нет whitelist в AppProject, нет HPA; pause/resume уже работает |
| Cold start 100–500 мс | на этом кластере реально секунды/десятки секунд; есть warm pool |
| `sandbox="allow-scripts allow-same-origin"` | classic sandbox bypass |
| `companies.tenant_db_connection` | единая точка компрометации всех компаний |
| egress плагина на `postgresql:5432` | противоречит ADR-003 и as-built «Platform Postgres: Never» |
| wildcard `*.agentscale.dev` сертификат | в кластере self-signed TLSStore, cert-manager нет |
| `v1→v4 deprecate` при «всё под нож» | разведено: цель = удаление, метод = strangler |
| Дубли §9.2/§9.6, §9.3/§9.7, два `## 10.` | удалены |
| Два ADR с номером 002 | новый → ADR-006, см. реестр |
| Токен в URL `?token=...` | утечка через Referer/историю/логи; только заголовок или `postMessage` |
| Клиентские заголовки после доверенных в proxy | allowlist + отбрасывание входящих `X-Plugin-*`/`Cookie` |
| Анонимный `pluginpage_proxy` (`pass`) | обязательная сессия + проверка grant + `employee ∈ company` |
| Статический Envoy-кластер `webhooks.example.com` | кластеры строятся контроллером из подписанного токена |
| Долгоживущие `TENANT_DB_DSN`/токен в env | короткие lease + `secret_ref` (ADR-003) |
| Просроченный `exp`, самореференция `artifact_digest` в манифесте | исправлены |
| Generic Redis `command+args`, версия плагина в имени схемы | typed контракты; версия только в журнале миграций |

---

## 14. Порядок реализации

1. ADR-реестр + переименование ADR-006 + статусы Superseded.
2. Расширение `CapabilityGatewayService` / `CapabilityGrant` новыми capability.
3. `TenantUserDbService`: схема `tenant_<company>_module_<module>`, структурированный манифест.
4. Liquibase Job + валидатор changelog + `migrations/plan|apply|rollback`.
5. Egress-контроллер (динамический allowlist) + единая схема аудита.
6. Plugin Gateway + Ingress (priority 120) + тест приоритетов.
7. Lifecycle-контроллер (pause/resume/wake) на базе `SandboxClaim`.
8. Артефакт/реестр/подпись + установка + grant/bind.
9. Пилотный плагин за фича-флагом + dual-read метрики.
10. Snapshot/бэкап до переноса данных → strangler по контекстам → cutover → удаление declarative-app.

---

**Конец документа.** Дизайн, не код. Реализация через субагентов после утверждения §2.3 (вариант A или B) и §11.
