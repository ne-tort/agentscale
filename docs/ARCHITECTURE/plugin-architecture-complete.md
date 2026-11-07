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
| Structured DB без raw SQL | `TenantUserDbService`: БД `agentscale_userdb`, схема `p_<project>`, `_ALLOWED_TYPES`, create/select/insert/drop/purge. **Для плагинов НЕ используется** — это и есть ограниченная декларативность, от которой уходим; остаётся как as-built для агентов | `application/tenant_infra/userdb_service.py:1-172`, `infra/k3s/overlays/dev/userdb-init.yaml:42-48` |
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

## 2. Модель данных (выбран вариант B: прямой DSN)

> **Решение (2026-11-08):** выбран **прямой доступ плагина к своей БД**. Это более
> профессионально для задачи «модуль = полноценное приложение» и не потребует
> переделки. Вариант A (typed gateway, `TenantUserDbService._ALLOWED_TYPES`) — это
> по сути та же «ограниченная декларативная хрень», от которой уходим: он запрещает
> произвольный SQL, JOIN, CTE, `EXPLAIN`, собственные индексы и миграции.
> ADR-003 отменяется в части плагинов — см. §2.6.

### 2.1 Один кластер, БД на компанию

```
PostgreSQL (отдельный инстанс для tenant-данных, НЕ prodavan)
├── prodavan                 — платформенная БД, Alembic, SoT приложения.
│                              Плагину недоступна: отдельный хост/порт, отдельные
│                              credentials, NetworkPolicy запрещает egress.
└── tenant_<company_id>      — отдельная БД на компанию (создаётся при создании компании)
    ├── schema module_<module_id>          — схема плагина (БЕЗ версии в имени)
    │   ├── реальные таблицы плагина: typed columns, индексы, constraints, FK
    │   ├── DATABASECHANGELOG / plugin_migrations
    │   └── данные: company-wide + cabinet_id / project_id колонки
    ├── schema module_<module_id>_...      — другие плагины компании
    └── search_path = "module_<module_id>"  — зафиксирован в роли плагина
```

**Почему отдельная БД на компанию, а не общая схема:** при прямом DSN это и есть
настоящая граница. Роль плагина имеет `CONNECT` только на свою БД; `pg_hba.conf`
и `GRANT CONNECT` физически не дают ей подключиться к `prodavan` или к
`tenant_<другая_компания>`. Никакой RLS, GUC или доверия к адаптеру не требуется:
утечка в чужую БД исключена **конструктивно**, на уровне PostgreSQL, а не политикой.

**Почему один кластер, а не инстанс на компанию:** single-node k3s
(`ResourceQuota pods: 45`, 26Gi, три OOM-инцидента в `runbook.md:484-554`).
Один инстанс + N БД даёт ту же логическую изоляцию без умножения control plane.
Отдельный инстанс — enterprise-tier с выделенной нодой, не v1.

**Что не так с опасениями v2:**

| Опасение | Почему снимается |
|---|---|
| «N БД = N целей миграций» | Так и надо: плагин мигрирует **свою** схему в **своей** БД, это его ответственность. Платформенная цепочка Alembic относится только к `prodavan` и не трогается |
| «Нет PgBouncer, max_connections=100» | Ставится PgBouncer в transaction mode **на tenant-кластер**; пул один на кластер, а не N пулов. `CONNECTION LIMIT` на каждой роли ограничивает число сессий плагина |
| «Нет max_size / бэкапов» | Разработка на dev: данные эфемерны по определению, бэкапы и перенос данных **не нужны**. Размер БД — observability (`pg_database_size()`), не требование |
| «Нет бэкапов» | Не требуется, см. §8 |

### 2.2 Плагин владеет своей схемой

Плагин получает DSN **своей компании** и роль, которая:

```sql
CREATE ROLE tenant_co_123_plugin_mod_equipment
  LOGIN PASSWORD '...'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 10;

GRANT CONNECT ON DATABASE tenant_co_123 TO tenant_co_123_plugin_mod_equipment;

-- Схема создаётся платформой при install, владелец — роль плагина.
CREATE SCHEMA module_mod_equipment
  AUTHORIZATION tenant_co_123_plugin_mod_equipment;

ALTER ROLE tenant_co_123_plugin_mod_equipment
  SET search_path = 'module_mod_equipment';
ALTER ROLE tenant_co_123_plugin_mod_equipment
  SET statement_timeout = '30s';       -- дефолт сессии, не «защита», а предохранитель
ALTER ROLE tenant_co_123_plugin_mod_equipment
  SET lock_timeout = '10s';
```

Далее плагин **сам** делает `CREATE TABLE`, `ALTER TABLE`, индексы, FK, миграции —
полноценный SQL, без `_ALLOWED_TYPES` и структурированных манифестов. Это ровно то,
чего не хватало declarative-app.

**Границы при этом жёсткие:**

- `NOCREATEDB NOCREATEROLE` —不能 создать БД/роль, не может выдать себе права.
- `CONNECT` только на `tenant_co_123`; `prodavan` и чужие БД недоступны.
- `search_path` зафиксирован, `CREATE` только на свою схему; чужие схемы компании
  не видны (нет `USAGE`).
- `NetworkPolicy`: egress пода плагина → только tenant PostgreSQL + gateway `:8001`
  + egress-proxy. Платформенный Postgres в принципе не достижим с пода плагина.
- `pgaudit` на tenant-кластере: все DDL/DML пишутся в append-only лог.

### 2.3 Роль остальной инфраструктуры

Прямой DSN выдаётся **только к БД плагина**. Всё остальное — по-прежнему через
универсальные контракты адаптеров, без знания реализации:

| Ресурс | Как плагин получает |
|---|---|
| Свои таблицы | **прямой DSN**, полный SQL |
| Cache / Redis | `cache` capability, key-rewrite, TTL, квоты |
| Objects / MinIO | `objects` capability, tenant-теги, без presigned |
| Docs / Mongo, Events, MCP, Pods, Secrets, Network | typed adapters gateway `:8001` |
| Платформенная БД `prodavan` | **недоступна вообще** |

То есть «БД лучше иметь прямой доступ» удовлетворено буквально, а «к остальной
инфраструктуре через адаптеры» сохранено.

### 2.4 RLS не нужен

При БД-на-компанию RLS по `tenant_id` защищал бы от той же компании — бессмысленно.
`ENABLE ROW LEVEL SECURITY` без `FORCE` не действует на владельца, а владелец здесь —
плагин; `app.*` GUC он же и пере-set'ит. Поэтому межтенантовая граница = **отдельная
БД + роль + `CONNECT` + NetworkPolicy**. RLS не применяется. Для разделения по
`cabinet_id`/`project_id` внутри БД компании плагин сам строит `WHERE` — это его
бизнес-логика, а не платформенная политика.

### 2.5 Миграции (Liquibase) при прямом DSN

Плагин применяет changelog **сам, под своей ролью** при старте/установке — это часть
«полноценного приложения». Платформа не парсит и не ревьюит его SQL:

```
backend startup
  → Liquibase update (changelog из подписанного артефакта)
  → DATABASECHANGELOG в схеме плагина
  → healthcheck /health после успешного apply
```

Что остаётся за платформой:
- подпись артефакта и проверка digest до запуска (§5.2);
- изоляция роли (`NOCREATEDB`, `search_path`, `CONNECT`);
- `pgaudit` и наблюдение за DDL как за фактом;
- egress Job/пода без интернета;
- `plan`/`rollback` доступны плагину как обычные Liquibase-команды.

Статический валидатор запрещённых statement **убирается**: при `NOCREATEDB` и
отдельной БД `DROP DATABASE`, `CREATE ROLE`, `COPY ... TO PROGRAM` (требует
superuser/pg_execute_server_program) и так невозможны. Проверка безопасности
перенесена туда, где она действительно работает — в привилегии PostgreSQL.

### 2.6 Отмена ADR-003 в части плагинов

ADR-003 («Pod и агент не получают прямых credentials») **продолжает действовать для
агентов**. Для plugin-контейнеров вводится исключение: прямой DSN к **собственной
БД компании**, при соблюдении §2.2. Требуется отдельная запись в реестре ADR
(`ADR-007`) с формулировкой Supersedes-where-applicable; до её принятия реализация
§2 не начинается.

---

## 3. Миграции схемы (Liquibase и аналоги)

### 3.1 Принцип

**Плагин владеет своей схемой и мигрирует её сам** — это прямое следствие выбранного
варианта B (§2). Платформа не ревьюит SQL плагина и не подменяет его структурированным
манифестом: безопасность обеспечивается привилегиями PostgreSQL и отдельной БД, а не
парсером запросов.

```
Артефакт плагина (подписан)
└── migrations/
    ├── liquibase/changelog.xml      ← рекомендуемый
    ├── alembic/versions/            ← для Python-плагинов
    └── flyway/V1__init.sql          ← альтернатива
```

```bash
# В backend-контейнере плагина при старте (не HTTP-запрос к платформе)
liquibase \
  --url="$TENANT_DB_DSN" \
  --changeLogFile=/migrations/liquibase/changelog.xml \
  --contexts=prod --labels=v1.2 \
  update
```

`DATABASECHANGELOG` лежит в схеме плагина. `/health` поднимается только после
успешного `update`; при ошибке миграции контейнер не становится Ready.

### 3.2 Что контролирует платформа

Контроль перенесён на уровень, где он действительно работает:

| Контроль | Где |
|---|---|
| Роль не может создать БД/роль, выдать себе права | `NOCREATEDB NOCREATEROLE NOSUPERUSER`, §2.2 |
| Роль не может выйти из своей БД | `GRANT CONNECT` только на `tenant_<company>` + `pg_hba.conf` |
| Роль не может писать в чужую схему | `search_path` + отсутствие `USAGE` на другие схемы |
| Pod не может достучаться до `prodavan` | NetworkPolicy: egress только tenant PG + gateway + egress-proxy |
| DDL фиксируется | `pgaudit` на tenant-кластере → append-only лог |
| Артефакт не подменён | digest + Ed25519-подпись до старта контейнера (§5.2) |
| Shell-команды из changelog | `LIQUIBASE_ALLOW_EXECUTE_COMMAND=false` |
| Ресурсы | `resources.limits` контейнера плагина, `CONNECTION LIMIT` роли |

Статический парсер SQL **не нужен**: `DROP DATABASE`, `CREATE ROLE`,
`COPY ... TO PROGRAM`, `pg_read_file` в этой конфигурации невозможны по привилегиям.
Попытка трактуется как нарушение и видна в `pgaudit`.

### 3.3 Дополнительные операции

| Операция | Кто и как |
|---|---|
| `plan` / dry-run | `liquibase updateSQL` внутри контейнера плагина, без применения |
| `rollback` | `rollbackCount` / `rollbackTo` плагином; требуется явный DOWN |
| Checksum mismatch | Liquibase отказывает в `update`, контейнер не становится Ready |
| Повторный запуск | идемпотентно по `DATABASECHANGELOG` |

Версия плагина в имени схемы **не** используется (иначе при обновлении данные
останутся в старой схеме); версия живёт в `DATABASECHANGELOG` и в пути статики.

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
| `db.direct` | прямой DSN к **своей** БД компании: полный SQL, JOIN, CTE, DDL, индексы, миграции | граница — отдельная БД + роль `NOCREATEDB NOCREATEROLE` + `CONNECT` + NetworkPolicy + `pgaudit`. В gateway **не** проходит |
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
| capability | `db.direct` (своя БД), `db.migrations`, `cache`, `objects`, `docs`, `events`, `jobs`, `pods`, `mcp`, `secrets`, `network` | `CapabilityGrant` для адаптеров; для `db.direct` граница — привилегии PostgreSQL, а не gateway |
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
3. Backend → контейнер по образу из манифеста; env/k8s Secret получает `GATEWAY_URL`,
   `PLUGIN_ID`, короткий bridge lease и **`TENANT_DB_DSN`** (только своей БД компании).
   Пароль — `secret_ref` с ротацией при reinstall, не plaintext в манифесте.
4. Старт контейнера: Liquibase `update` по §3 → `/health` Ready.
5. Grant/bind → запись в `CapabilityGrant` + `PodBridgeClaims` (для адаптеров, не для БД).
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
| `POST /gateway/plugins/{id}/grant` | создаёт БД `tenant_<company>` (если нет), схему `module_<id>` с owner = роль плагина, роль, `CapabilityGrant` для адаптеров, маршруты и egress |
| `POST /gateway/plugins/{id}/revoke` | отзыв bridge lease, снятие маршрутов, `Suspended`; БД/схема сохраняются |
| `POST /gateway/plugins/{id}/uninstall` | `purge_project_tenant_infra`, `DROP SCHEMA`, дроп роли, удаление артефакта. Данные dev эфемерны — snapshot **не требуется** |
| `POST /gateway/plugins/{id}/wake` | см. §7.6; новый короткий bridge lease; DSN остаётся тем же |

`migrate-to-plugin` **исключён**: перенос данных не нужен, это dev-разработка.

Секреты: `TENANT_DB_DSN` и bridge lease — короткоживущие `secret_ref` с ротацией при
reinstall/revoke; plaintext в манифесте запрещён. ADR-003 действует для агентов,
для плагинов отменён в части `db.direct` (§2.6, ADR-007).

---

## 6. Сеть и фаерволл (единая модель для плагинов и агентов)

### 6.1 NetworkPolicy

Исправления относительно v1: `namespaceSelector` по `kubernetes.io/metadata.name` (лейбла `name` в `namespace.yaml` нет); egress к **tenant** PostgreSQL разрешён подам плагина (это и есть выбранный прямой DSN), ноPlatform Postgres `prodavan` остаётся недостижим — отдельный Service/хост, отдельные credentials, правило ниже его не включает.

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
    # Tenant PostgreSQL: отдельный Service, НЕ prodavan.
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: agentscale-dev}
          podSelector:
            matchLabels: {app: tenant-postgres}
      ports: [{protocol: TCP, port: 5432}]
```

`prodavan` (платформенный Postgres) в эту политику **не входит**: у плагина нет ни
сетевого пути, ни credentials, ни `CONNECT` к этой БД. Tenant-кластер — отдельный
Deployment/Service с собственным Secret; роли создаются platform-админом при grant.

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
| Миграции | `manifest.migrations` | старт контейнера плагина, свой DSN | `liquibase update` в entrypoint, не платформенный Job |

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

Разрешаем противоречие v1: **«всё под нож» — про конечное состояние, а не про способ перехода.** В финале declarative-app (`module_meta_documents`, generic runtime, `module_instance_data_rows`, `ModuleRuntime.tsx`) удалён полностью. Параллельный прогон нужен **только из-за объёма кода**, а не из-за данных:

- `mod_equipment` — ~13k строк: 18 Python-файлов (9 678 строк) + 16 TS/TSX (3 485 строк), отдельная страница `/equipment`, Celery beat/sweep, MCP-сервер, OpenSearch-индексы.
- Это **dev-разработка**: данные эфемерны, бэкапы не нужны, перенос данных не нужен, DOWN-миграции платформы не требуются. Любые тестовые данные создаются заново сидом.

**Этапы:**

1. **Инфраструктура** — tenant PostgreSQL (отдельный Deployment/Service/Secret), роли/БД при grant, PgBouncer в transaction mode, `pgaudit`, egress-контроллер, Ingress с `router.priority: 120`, Plugin Gateway, lifecycle-контроллер. Declarative-app работает как прежде.
2. **Пилот** — первый плагин с собственными таблицами и Liquibase-миграциями за фича-флагом `plugin_enabled_modules[]`. Данные — только тестовые, созданные сидом плагина.
3. **Перенос frontend** — страница плагина в iframe; backend-логика пока в API.
4. **Перенос backend по одному bounded context'у** — офферы → цены → бюджет → документы → тендеры. Каждый шаг за флагом; старый путь можно просто выключить, сравнивать нечего.
5. **Cutover** — переключение флага.
6. **Удаление** — убрать declarative-app, `mod_management` перевести на plugin, старые таблицы `module_*` дропаются ревизией Alembic без переноса содержимого.

Ни `migrate-to-plugin`, ни snapshot, ни dual-read/dual-write, ни «сверка контрольных сумм», ни «N недель наблюдения» — **не требуются**. Все доводы v2 про потерю данных были про prod-данные, которых на dev нет.

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
| Произвольный DDL затирает чужие БД | отдельная БД на компанию + `GRANT CONNECT` только на неё + `NOCREATEDB NOCREATEROLE NOSUPERUSER`; `pgaudit` фиксирует DDL; платформенный Postgres в другом Service и недостижим по NetworkPolicy |
| Утечка между компаниями | исключена конструктивно: роль не имеет `CONNECT` к чужой БД. RLS/GUC не участвуют |
| Подмена `X-Plugin-*` внешним клиентом | Gateway строит headers allowlist'ом, отбрасывает входящие `X-Plugin-*`/`Cookie`/hop-by-hop; backend дополнительно проверяет JWT |
| Анонимный доступ к `/pluginpage/*` | обязательная сессия + `employee ∈ company` + grant; `public_pages` — явное исключение с урезанным scope |
| iframe снимает sandbox | отдельный origin, без `allow-same-origin`, restrictive CSP, `plugin-rpc/1` handshake, проверка `event.origin`; токен не в URL |
| Исчерпание соединений/памяти PG | один PgBouncer (transaction mode) на tenant-кластер, `CONNECTION LIMIT` на роли, `statement_timeout`/`lock_timeout` как предохранители |
| DoS через egress | `none`/`webhooks-only`/`full`, динамический allowlist из токена, квота байт, аудит |
| `/webhook/` уезжает в SPA | `router.priority: 120` + entrypoints + ingressClassName + тест приоритетов |
| Сломанный cutover | strangler + фича-флаг; данные dev эфемерны, откат = выключить флаг |
| Расхождение политик | один egress-контроллер, один lifecycle-контроллер, один реестр capability |

---

## 11. Acceptance-критерии (готовность к реализации)

Дизайн считается утверждённым, когда по каждому пункту есть тест или проверка в CI/Verify Dev. Тесты пишутся вместе с кодом, но не запускаются до явного решения о начале верификации.

| # | Критерий | Проверка |
|---|---|---|
| 1 | Роль плагина владеет **своей** схемой, но `NOCREATEDB NOCREATEROLE NOSUPERUSER` | `\du`; попытка `CREATE DATABASE` / `CREATE ROLE` → `42501` |
| 2 | Роль не может подключиться к `prodavan` или чужой `tenant_*` БД | integration: `psql "$prodavan_dsn"` и `tenant_<other>` → `FATAL: permission denied` |
| 3 | Плагин сам применяет Liquibase changelog; повторный запуск не меняет схему | integration: `DATABASECHANGELOG` без новых строк |
| 4 | `/health` не становится Ready при ошибке миграции / checksum mismatch | e2e контейнера плагина |
| 5 | `pgaudit` содержит DDL плагина | integration по логам tenant-кластера |
| 6 | Анонимный `/pluginpage/{plugin}/{company}/api/*` без сессии → `401` | e2e Gateway |
| 7 | Webhook без/с неверной `X-Prodavan-Signature` → `401`; повтор `X-Prodavan-Delivery` не дублирует обработку | e2e по `docs/external-api.md` |
| 8 | Egress на хост вне `allowed_hosts` → `403` + событие аудита | e2e egress-контроллера |
| 9 | `/webhook/` и `/pluginpage/` не уезжают в SPA | тест приоритетов по образцу `test_ingress_priority.py` |
| 10 | Превышение `ops_per_minute` → `429` + `QUOTA_EXCEEDED` | unit/integration gateway |
| 11 | Audit-событие не содержит raw SQL, параметров, токенов, payload | unit-тест redaction по `audit-append-shape-decision.md` |
| 12 | `revoke` мгновенно делает старый токен невалидным | integration `bump_pod_bridge_generation` |
| 13 | Wake-up после `Suspended` выдаёт новый lease, а не истёкший из Secret | integration lifecycle |
| 14 | Активный `job_id` блокирует усыпление backend | integration lifecycle |
| 15 | Uninstall дропает схему/роль, не трогая чужие БД | integration lifecycle |
| 16 | Фича-флаг `plugin_enabled_modules[]` переключает путь без миграции данных | e2e: старый/новый путь работают независимо |

---

## 12. Открытые вопросы

1. **Хранение артефактов** — реестр с подписью: GHCR + cosign/Ed25519 или отдельный MinIO-префикс?
2. **Публичные страницы** — какие данные допустимы без сессии; формат anon-scope.
3. **Cross-module sharing** — пока запрещено; если понадобится, только через явный grant в gateway.
4. **Маркетплейс** — публикация/установка плагинов, отдельный документ.
5. **Billing** — привязка `quota_ref` к тарифу.
6. **ADR-007** — явное supersede ADR-003 в части `db.direct` (обязателен до реализации §2).

Закрытые вопросы: вариант доступа к БД выбран (B, прямой DSN); бэкапы, перенос данных, dual-read, snapshot и «N недель наблюдения» исключены — это dev-разработка, данные эфемерны.

---

## 13. Что отвергнуто в v1 и почему

| Было в v1 | Почему отвергнуто |
|---|---|
| `CREATE DATABASE tenant_<company_id>` | **принято в v3** при прямом DSN: это и есть настоящая граница изоляции. Один кластер + PgBouncer, N БД |
| Прямой `TENANT_DB_DSN` плагину | **принято в v3**; ADR-003 отменяется в части плагинов (ADR-007). Риски RLS/GUC снимаются тем, что RLS не используется, а граница — `CONNECT` |
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
| egress плагина на `postgresql:5432` | **принято в v3** для отдельного tenant-Postgres Service; платформенный `prodavan` по-прежнему недостижим |
| `companies.tenant_db_connection` | единая точка компрометации: учётные данные хранятся в k8s Secret на компанию, а не в общей таблице |
| wildcard `*.agentscale.dev` сертификат | в кластере self-signed TLSStore, cert-manager нет |
| `v1→v4 deprecate` при «всё под нож» | разведено: цель = удаление, метод = strangler |
| Дубли §9.2/§9.6, §9.3/§9.7, два `## 10.` | удалены |
| Два ADR с номером 002 | новый → ADR-006, см. реестр |
| Токен в URL `?token=...` | утечка через Referer/историю/логи; только заголовок или `postMessage` |
| Клиентские заголовки после доверенных в proxy | allowlist + отбрасывание входящих `X-Plugin-*`/`Cookie` |
| Анонимный `pluginpage_proxy` (`pass`) | обязательная сессия + проверка grant + `employee ∈ company` |
| Статический Envoy-кластер `webhooks.example.com` | кластеры строятся контроллером из подписанного токена |
| Долгоживущие `TENANT_DB_DSN`/токен в env | короткие lease + `secret_ref`; DSN ротируется при reinstall/revoke |
| Generic Redis `command+args`, версия плагина в имени схемы | typed контракты; версия только в `DATABASECHANGELOG` и пути статики |
| `TenantUserDbService._ALLOWED_TYPES` / структурированный манифест схемы | это та же ограниченная декларативность: плагин в v3 владеет схемой и пишет полноценный SQL |
| Изолированный Liquibase Job под доверенной ролью + парсер запрещённых SQL | плагин мигрирует сам; безопасность = привилегии PostgreSQL + отдельная БД |
| `migrate-to-plugin`, dual-read/dual-write, snapshot, бэкапы, «N недель наблюдения» | не нужны: dev-разработка, данные эфемерны, переноса нет |
| Dual-read метрики парности | не нужны по той же причине; strangler нужен только из-за объёма кода |

---

## 14. Порядок реализации

1. ADR-реестр, ADR-006, **ADR-007** (supersede ADR-003 в части `db.direct`).
2. Tenant PostgreSQL: отдельный Deployment/Service/Secret, PgBouncer (transaction mode), `pgaudit`.
3. Lifecycle grant/revoke/uninstall: `CREATE DATABASE tenant_<company>`, роль, схема `module_<id>` с owner = плагин, `search_path`, `CONNECT`.
4. Расширение `CapabilityGatewayService` / `CapabilityGrant` для **не-БД** адаптеров (cache, objects, docs, events, jobs, pods, mcp, secrets, network).
5. Egress-контроллер (динамический allowlist) + единая схема аудита + CSP/origin для iframe.
6. Plugin Gateway + Ingress (`router.priority: 120`) + тест приоритетов + webhook-контракт.
7. Lifecycle-контроллер (pause/resume/wake) на базе `SandboxClaim` + warm pool.
8. Артефакт/реестр/подпись + установка + Liquibase `update` при старте контейнера.
9. Пилотный плагин за фича-флагом `plugin_enabled_modules[]` с собственными таблицами и тестовыми данными.
10. Strangler по контекстам `mod_equipment` → cutover → удаление declarative-app (`module_*` дропаются ревизией без переноса данных).

---

**Конец документа.** Дизайн, не код. Реализация через субагентов на main; микрокоммиты `git add -A && git commit && git push`, без гигиены и без переписывания истории; тесты пишутся, но не запускаются до явного решения. Блокирующее условие — принятие ADR-007.
