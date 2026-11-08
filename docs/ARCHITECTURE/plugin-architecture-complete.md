# Plugin Architecture — Полный дизайн (v4)

**Дата:** 2026-11-08 (v4: пересмотр «переиспользования as-built»)  
**Статус:** целевая модель, подлежит утверждению перед реализацией.  
**Принцип:** в конечном состоянии declarative-app удалён полностью («всё под нож»).  
**Метод:** strangler с фича-флагом, а не big bang — см. §8.

> История версий: v1 отвергнут (изобретал уже существующую инфраструктуру,
> неработающая безопасность); v2 исправил ошибки v1, но ошибочно выбрал typed
> gateway без DSN; v3 вернул прямой DSN и убрал прод-логику про бэкапы.
> **v4 устраняет главную ошибку v2/v3: «as-built есть → значит переиспользуем как
> есть».** Это неверно. Переиспользуются **код и контракты**; точки привязки
> identity, транспорта, хранилища, артефактов и lifecycle **заменяются** на
> профессиональную модель. Подробности — §1 и §13.

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

## 1. Что переиспользуется, что расширяется, что заменяется

**Принцип v4:** наличие работающего кода не является причиной тащить его как есть.
Плагин — **иной principal** (company-scoped, stateless, HTTP-driven, со своей БД),
чем агентский Pod (project-scoped, stateful, с workspace и файлами). Общими должны
быть **контракты и код проверок**, а не trust-модель и транспорт.

| Компонент as-built | Решение v4 | Обоснование |
|---|---|---|
| `CapabilityGatewayService.authorize/invoke`, `DecisionCode`, `AuditEvent`, `CapabilityGrant` | **Переиспользовать код** | Это зрелый fail-closed pipeline identity → scope → grant → quota → adapter (`application/capability_gateway_service.py:180-255`). Ценность именно в ядре, а не в том, на каком порту оно висит |
| pod-gateway `:8001` (`prodavan.main_pod:app`) | **Оставить агентам, НЕ расширять для плагинов** | Trust = Bridge JWT `typ=pod_bridge`, `sub=pod:{pod_id}`, `require_project()`, allowlist `projects/{id}/(infra\|modules\|agent)/` (`core/middleware.py:36-38`, `pod_identity/bridge.py:76-88,206-232`). Плагин company-scoped; смешение даёт ему доступ к `/agent/*` и `/internal/pods/{pod_id}/workspace-archive` |
| `PodBridgeClaims` / `mint_pod_bridge_token` | **Заменить для плагинов** на `PluginClaims` (`iss=agentscale-plugins`, `aud=capability-gateway`, `sub=plugin:<id>:<company>:<install>`, `version`, `artifact_digest`, `grant_id`, `jti`, короткий TTL) | В Bridge JWT нет instance/artifact/version, revocation завязана на `pod_id`+Redis, общий HS256-секрет с dev-фолбэками (`bridge.py:111-128`). Отзыв плагина = revoke grant, не bump generation пода |
| Один MinIO-бакет `agentscale` + tenant-теги в БД | **Заменить изоляцию**: bucket-per-tenant (или префикс + MinIO STS `AssumeRole` с session policy), short-lived scoped credentials/presigned. `TenantObjectsService` оставить фасадом аудита/квот/листинга | Теги `tenant_infra`/`project:` — это Python-фильтр в `objects_service.py:40-50`, а не IAM; один service user имеет `agentscale/*` целиком (`minio-api-policy.json:6-13`). Баг в коде = утечка. Presigned уже используются для обычного контента (`minio_store.py:186-203`), «намеренно отвергнуты» относилось только к Pod-surface |
| Celery-outbox `webhook_deliveries` | **Переиспользовать как есть** для исходящих вебхуков; **не** использовать как шину событий плагинов | Outbox + `FOR UPDATE SKIP LOCKED` — зрелый паттерн транзакционной доставки (`external-api.md:41-49`), но это не durable log с replay |
| Redpanda/Kafka (`agentscale-kafka:9092`, включён) | **Расширить**: топик `agentscale.plugin.events`, consumer groups с ручным commit, `earliest`, DLQ + 3 retry, ключ = `company_id` | Сейчас `KAFKA_CONSUMER_MODE=kick`, `enable_auto_commit=True`, `auto_offset_reset=latest` (`configmap.yaml:35-41`, `kafka_manager.py:694-696`) — это «будильник», события теряются при простое консьюмера |
| `SandboxClaim` / `SandboxWarmPool` (агенты) | **НЕ переиспользовать для плагинов** | CRD не несёт `image`/`port`, требует `warmPoolRef`, `envVarsInjectionPolicy: Disallowed`, тащит PVC 5Gi, `ensure_running` не ждёт Ready (`agent_sandbox/runtime.py:336-348`), пул греет образ агента, не плагина |
| Traefik Ingress + `router.priority` | **Переиспользовать механизм**, path-based v1 → wildcard subdomain как цель | Приоритет — аннотация на Ingress (`infra/ops/tests/test_ingress_priority.py`); cert-manager в кластере нет, TLS = self-signed TLSStore (`k3s-services.md:117-121`) |
| CI `ghcr.io` + build-push-action | **Расширить**: включить `provenance`/`sbom` (сейчас `false`), добавить cosign keyless, pin по digest | `ci-images.yml:114-115,229-230` явно отключают provenance/SBOM, cosign/sigstore нет нигде, dev гонит `:latest` |
| Доменная модель `AppVersion` / `RegistryOutcome` / `RollbackCommand` / `QuarantineEvidence` | **Переиспользовать** как реестр плагинов | `domain/dynamic_apps/types.py:135-192`, `dynamic_app_registry/contracts.py:13-99` уже описывают digest, semver, quarantine, rollback — изобретать свой реестр не нужно |

**Что действительно переиспользуется без изменений:** ядро `CapabilityGatewayService`,
схема `AuditEvent`, HMAC-контракт вебхуков, outbox-доставка, Celery beat для cron,
Redpanda как транспорт, Traefik/TLSStore, Argo app-of-apps и `ignoreDifferences`
(не CRD!), доменный реестр `dynamic_app_registry`, strangler-подход.

**Что запрещено «просто добавить»** без правки `infra/argocd/apps/00-project.yaml`
(жёсткий whitelist 27 kinds) и `app-budget.yaml` (45 подов / 12Gi requests / 26Gi
limits, 3 OOM-инцидента): KEDA, Knative, cert-manager, SPIRE, Temporal, HPA.
Ни один из этих control plane в v4 не вводится.

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
| Артефакт не подменён | `cosign verify` по digest до старта контейнера (§5.1) |
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

### 4.1 Два trust-контура, одно ядро проверок

Плагин — **не агентский Pod**. Агент: `sub=pod:{pod_id}`, project-bind, эфемерная
сессия, workspace, файлы. Плагин: company-scoped, stateless, instance + версия +
артефакт, свой DSN, HTTP-driven. Смешивать их в одном токене и одном allowlist —
ошибка конфигурации, дающая плагину доступ к `/agent/*` и
`/internal/pods/{pod_id}/workspace-archive` (`core/middleware.py:36-38`).

Поэтому:

- **`:8001` pod-gateway остаётся агентам без изменений.** Его trust (`typ=pod_bridge`,
  `aud=prodavan-pod-bridge`, `require_project()`, `claims.pod_id == path`) не
  трогается.
- **Плагины идут в Plugin Gateway** (тот же always-running сервис, что проксирует
  `/webhook/` и `/pluginpage/`, §7.2) на отдельном route-group `/capabilities/v1/*`
  с **своим** issuer/audience/ключом. Отдельный сервис `capability-adapters:9000`
  **не строится** — это был бы дубль ядра.
- Переиспользуется **код** `CapabilityGatewayService.authorize/invoke`,
  `DecisionCode`, `AuditEvent`, `CapabilityGrant`, доменная модель
  `dynamic_app_registry` и `canonical_grants`. Добавляется отдельная ветка
  allowlist middleware, различающая типы токенов.
- mTLS/SPIFFE отложены: на single-node k3s с 45 подами и 3 OOM-инцидентами это
  несоразмерный control plane. Достаточно short-lived signed token c `kid`/JWKS и
  привязкой к `artifact_digest`.

### 4.2 PluginClaims (новый контракт identity)

```json
{
  "iss": "agentscale-plugins",
  "aud": "capability-gateway",
  "typ": "plugin_capability",
  "sub": "plugin:mod_equipment:co_123:inst_abc",
  "company_id": "co_123",
  "cabinet_id": "cab_456",
  "project_id": null,
  "plugin_id": "mod_equipment",
  "plugin_instance_id": "inst_abc",
  "manifest_version": "1.2.0",
  "artifact_digest": "sha256:...",
  "grant_id": "grant_...",
  "scopes": ["cache", "objects", "events", "jobs", "pods", "mcp", "secrets", "network"],
  "quota_ref": "co_123/plan_pro",
  "jti": "...",
  "exp": 1794163200
}
```

Отличия от v3: `egress.allowed_hosts` и `quotas` **убраны из токена**. Egress-политика
и квоты — долгоживущие, хранятся в `CapabilityGrant` в БД; токен несёт только
`grant_id` и короткий TTL (300 с, новый при wake-up). Иначе истечение токена
пересобирало бы сетевые правила посреди запроса (`§6.2` в v3).

Отзыв = удаление/деактивация `CapabilityGrant` + проверка `grant_id` на каждом
запросе, а не `bump_pod_bridge_generation` (то привязано к `pod_id` и Redis).
Токен — только в `Authorization`, никогда в URL query.

### 4.3 Контракты адаптеров (типизированные, без generic raw SQL)

ADR-002-capability-gateway запрещает «generic row/action dispatch как публичный
контракт». Поэтому вместо `POST /adapters/db/query {"sql": "SELECT ..."}`:

| Адаптер | Контракт | Примечание |
|---|---|---|
| `db.direct` | прямой DSN к **своей** БД компании: полный SQL, JOIN, CTE, DDL, индексы, миграции | граница — отдельная БД + роль `NOCREATEDB NOCREATEROLE` + `CONNECT` + NetworkPolicy + `pgaudit`. В gateway **не** проходит |
| `cache` | `get/set/delete/incr/publish` с key-rewrite | `tenant:{company}:plugin:{module}:{instance}:*` (project — опционален) |
| `objects` | short-lived scoped credentials / presigned, bucket-per-tenant | см. §5.4; теги остаются метаданными, **не границей** |
| `docs` | Mongo namespace per tenant | typed запросы |
| `events` | `publish/subscribe` через Redpanda consumer groups | см. §7.3 |
| `jobs` | `submit/get/cancel` с `job_id`, `run_id`, lease | Celery + outbox |
| `pods` | `invoke` typed action | плагин не имеет k8s API |
| `mcp` | `register` typed tools | MCP = адаптер к gateway |
| `secrets` | `read` по `secret_ref` | плагин не хранит plaintext |
| `network` | egress-прокси | см. §6 |

Квоты: `CompanyTenantInfraQuota` **расширяется** до per-`(company, plugin_instance,
artifact_digest)` — иначе один «шумный» плагин исчерпает лимит компании, а
project-ключ `enforce_ops_rate` (`quota.py:93-107`) для company-wide плагина не имеет
смысла.

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

### 5.1 OCI-артефакт как единственная единица поставки

**Замена v3:** `tar.gz + Ed25519 + самописный реестр ключей` → **OCI в GHCR + cosign**.
Причина: в v3 подписанный manifest ссылался на **неподписанный** backend-образ
`ghcr.io/...:1.2.0`, а frontend доставлялся вторым каналом (MinIO). Плюс CI уже
имеет GHCR (`ci-images.yml:43,101-106`), но явно отключает `provenance`/`sbom`
(`:114-115`) и не содержит cosign.

```
ghcr.io/ne-tort/plugins/mod_equipment@sha256:<digest>   ← подписано cosign keyless (OIDC)
├── layer: backend image (linux/amd64)
├── layer: frontend/dist (OCI artifact, mediaType application/vnd.agentscale.plugin.frontend)
├── layer: migrations/liquibase/changelog.xml
└── annotations: plugin_id, manifest_version, protocol, requestedCapabilities,
                 min_platform_version, artifact_digest
```

- **Подпись:** `cosign sign` по **digest**, не по тегу; `permissions: id-token: write`;
  `cosign verify --certificate-identity --certificate-oidc-issuer` перед созданием
  Deployment. SBOM + provenance включаются в CI.
- **Pin по digest**, `:latest` для плагинов запрещён (сейчас dev гонит `:latest`).
- **Реестр:** переиспользуется доменная модель `AppVersion` / `RegistryOutcome`
  (`QUARANTINED`) / `RollbackCommand` / `QuarantineEvidence` из
  `domain/dynamic_apps/types.py:135-192` и `dynamic_app_registry/contracts.py:13-99`.
  Свой реестр ключей Ed25519 удаляется.
- **Compatibility gate** до PUBLISHED: semver, protocol, `requestedCapabilities` против
  `CapabilityGrant`, `min_platform_version`, сканирование образа, CSP для frontend.
- **Quarantine/rollback:** неподписанный или несовпавший digest не публикуется;
  откат = деплой предыдущего immutable digest.

Manifest (подписан как OCI annotation, без самореференции):

```json
{
  "plugin_id": "mod_equipment", "version": "1.2.0",
  "frontend": {"entry": "index.html"},
  "backend": {"port": 8080, "health": "/health"},
  "capabilities": ["cache","objects","docs","events","jobs","pods","mcp","secrets","network"],
  "migrations": {"engine": "liquibase", "path": "migrations/liquibase/changelog.xml"},
  "egress_ref": "grant:...",
  "public_pages": [],
  "webhooks": ["/webhook/external-system"]
}
```

**Установка (идемпотентная):**
1. Реестр: `cosign verify` → digest → manifest → compatibility gate.
2. Frontend → **отдельный static-origin** (§5.2), версионированный путь.
3. Backend → Deployment `replicas: 0` по образу из того же OCI digest; Secret
   получает `GATEWAY_URL`, `PLUGIN_ID`, короткий `PluginClaims` lease и
   **`TENANT_DB_DSN`** только своей БД компании.
4. Старт контейнера: Liquibase `update` по §3 → `/health` Ready.
5. Grant → `CapabilityGrant` (в БД, долгоживущий) + `PluginClaims` (короткий).
6. Регистрация маршрутов и egress-правил из grant.

### 5.2 Frontend: отдельный origin, без `allow-same-origin`

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

### 5.3 Роутинг frontend: v1 static-origin, цель — wildcard subdomain

**Противоречие v3:** §5.2要求 «отдельный origin, не `agentscale.dev`», а §7.1 сажал
`/pluginpage/...` на тот же `agentscale.dev`.

- **v1 (прагматичный):** отдельный static-origin `plugin-origin.agentscale.dev`
  отдаёт **только** версионированную静statics без cookie и API; backend-вызовы идут
  на `agentscale.dev/pluginpage/.../api/*` через Plugin Gateway с `Authorization`
  (не cookie). `sandbox="allow-scripts allow-forms"` без `allow-same-origin` на
  path-based URL даёт opaque origin — это корректно, но `event.origin === "null"`,
  поэтому shell не шлёт туда токен. CSP и `plugin-rpc/1` handshake обязательны.
- **Цель:** `{company}--{plugin}.plugins.agentscale.dev` с wildcard TLS. Условия
  перехода: (1) реальный публичный домен и валидный TLS вместо self-signed
  TLSStore; (2) cert-manager + DNS-01 — in-tree солвера для reg.ru **нет**
  ([cert-manager DNS01](https://cert-manager.io/docs/configuration/acme/dns01/)),
  поэтому делегировать `_acme-challenge` на Cloudflare/acme-dns или проверенный
  webhook; (3) `cert-manager.io/{Certificate,ClusterIssuer}` добавлены в
  `00-project.yaml` whitelist и отдельный Argo App. Один wildcard, не N сертификатов.
  Subdomain снимает конфликт приоритетов Traefik и даёт настоящую изоляцию
  cookie/CSP/`event.origin`.

### 5.4 Объектное хранилище: IAM-граница вместо тегов

**Замена v3:** «один бакет `agentscale` + теги + API-mediated, presigned отвергнуты»
было верно для агентских Pod без egress к :9000, но **не** для backend-плагина.

| | Было (агенты) | Стало (плагины) |
|---|---|---|
| Изоляция | Python-фильтр по тегам `tenant_infra` / `project:` в `objects_service.py:40-50` | **bucket-per-tenant** `tenant-<company>` (v1-компромисс: префикс + MinIO STS `AssumeRole` session policy с `${aws:username}`) |
| Креденшелы | один service user с `agentscale/*` (`minio-api-policy.json:6-13`) | short-lived STS creds / scoped presigned, ограниченные `module-<id>/*` |
| Квота | счётчик `ContentAssetRow` в БД | bucket quota / usage, биллинг по одному бакету |
| Purge | цикл DELETE по списку | delete bucket/prefix |
| Egress | :9000 закрыт NP для Pod | :9000 разрешён **только** plugin-namespace |

`TenantObjectsService` остаётся фасадом аудита/квот/листинга, но перестаёт быть
единственной границей. Теги — метаданные/биллинг, не изоляция. Presigned уже
реализованы в `minio_store.py:186-203` для обычного контента; для внешних клиентов
сохраняется `proxy=1` (in-cluster DNS не резолвится снаружи).

### 5.5 Lifecycle: grant / revoke / uninstall

| Операция | Что делает |
|---|---|
| `POST /gateway/plugins/{id}/grant` | создаёт БД `tenant_<company>` (если нет), схему `module_<id>` с owner = роль плагина, роль, `CapabilityGrant`, маршруты, egress-правила из grant |
| `POST /gateway/plugins/{id}/revoke` | деактивация `CapabilityGrant` → мгновенный отзыв по `grant_id`; снятие маршрутов; `Suspended`; БД/схема сохраняются |
| `POST /gateway/plugins/{id}/uninstall` | purge tenant infra + delete bucket/prefix, `DROP SCHEMA`, дроп роли, удаление OCI-артефакта из реестра. Данные dev эфемерны — snapshot **не требуется** |
| `POST /gateway/plugins/{id}/wake` | см. §7.6; новый короткий `PluginClaims`, DSN остаётся тем же |

`migrate-to-plugin` **исключён**: перенос данных не нужен, это dev-разработка.

Секреты: `TENANT_DB_DSN` и `PluginClaims` — короткоживущие `secret_ref` с ротацией при
reinstall/revoke; plaintext в манифесте запрещён. ADR-003 действует для агентов,
для плагинов отменён в части `db.direct` (§2.6, ADR-007).

---

## 6. Сеть и фаерволл (общий генератор, две политики)

### 6.1 Две NP из одного CRD-генератора

v3 заявлял «единую NP для плагинов и агентов», но сам же добавлял плагину 5432 и
`:8080`, которых у агентов нет. Честная модель: **общая схема правил и единый
генератор**, две NetworkPolicy по `podSelector`.

- Агенты: существующая `agentscale-sandboxes-egress` — DNS + 80/443 + TCP 8001,
  без :9000 и без tenant-PG. **Не меняется.**
- Плагины: `plugin-egress` — DNS, Plugin Gateway `:8080`/capabilities, egress-proxy,
  **tenant-postgres :5432**, MinIO :9000 (§5.4). `prodavan` в правила не входит.

Источник egress-политики (`allowed_hosts`/`allowed_paths`) — **`CapabilityGrant` в
БД**, а не краткоживущий JWT: иначе истечение токена пересобирает сетевые правила
посреди запроса. Token несёт только `grant_id` (§4.2).

Аудит остаётся единым: `plugin_egress_request` и `agent_egress_request` имеют
одинаковую схему (§4.5).

### 6.2 NetworkPolicy плагина

`namespaceSelector` по `kubernetes.io/metadata.name` (лейбла `name` в `namespace.yaml`
нет). В отличие от v3, здесь **Plugin Gateway / capabilities**, а неpod-gateway
`:8001`; добавлен MinIO :9000 для scoped object access; `prodavan` не входит.

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
            matchLabels: {app: plugin-gateway}
      ports: [{protocol: TCP, port: 8080}]       # /capabilities/v1/*
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
    # Scoped object storage (§5.4); агентским Pod'ам :9000 закрыт.
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: agentscale-dev}
          podSelector:
            matchLabels: {app: minio}
      ports: [{protocol: TCP, port: 9000}]
```

`prodavan` (платформенный Postgres) в эту политику **не входит**: у плагина нет ни
сетевого пути, ни credentials, ни `CONNECT` к этой БД. Tenant-кластер — отдельный
Deployment/Service с собственным Secret; роли создаются platform-админом при grant.

### 6.3 Egress Proxy

| Режим | Поведение |
|---|---|
| `none` | только gateway + DNS |
| `webhooks-only` | разрешённые `allowed_hosts[]` × `allowed_paths[]` из **`CapabilityGrant` в БД** |
| `full` | любой egress, но с обязательным approval, логированием и квотой байт |

**Исправление v1/v3:** Envoy-кластер не статический (`webhooks.example.com`
захардкожен) и не строится из краткоживущего JWT. Контроллер читает allowlist из
`CapabilityGrant` по `grant_id` из токена; при отсутствии совпадения → `403` +
`direct_response`. DNS-резолв через отдельный egress-прокси (защита от обхода по
IP), проверка redirect и TLS на каждом хопе.

**Аудит:** `plugin_egress_request` / `agent_egress_request` — единая схема (host, path, method, status, bytes, decision, tenant, subject). Та же модель применяется к контейнерам агентов: «регулируемая изоляция контейнеров» достигается одним контроллером и одним набором правил, а не двумя реализациями.

### 6.4 Подмена заголовков

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
| Event handlers | `POST /gateway/events/subscribe` | Redpanda consumer group | `POST /events/*` (DLQ, 3 retry) |
| Background workers | `POST /gateway/tasks/submit` | Celery worker + outbox lease | `POST /tasks/*` |
| Миграции | `manifest.migrations` | старт контейнера плагина, свой DSN | `liquibase update` в entrypoint, не платформенный Job |

Backend — отдельный stateless контейнер, не имеет k8s API к кластеру, имеет
`TENANT_DB_DSN` (своя БД), общается с Plugin Gateway, tenant-PG и egress-proxy.

**Разделение транспортов (исправление v3):**

- **Outbox `webhook_deliveries` + `FOR UPDATE SKIP LOCKED` + lease** переиспользуется
  **как есть** — но только для исходящих вебхуков и входящих delivery, где нужна
  транзакционная доставка (`external-api.md:41-49`). Это не шина событий.
- **Redpanda/Kafka** (уже включён, `configmap.yaml:35-41`) становится транспортом
  событий плагинов: топик `agentscale.plugin.events`, ключ партиции = `company_id`,
  **ручной offset commit**, `auto_offset_reset=earliest`, DLQ + 3 retry,
  идемпотентность по `(subscription_id, source_event_id)`. Текущий режим
  `KAFKA_CONSUMER_MODE=kick` + `enable_auto_commit=True` (`kafka_manager.py:694-696`)
  теряет события при простое консьюмера — для плагинов он заменяется на dispatch.
- **Celery beat** остаётся для cron-расписаний (строки в `beat_schedule` + lock
  coalescing + `run_id`). KEDA/Temporal/k8s Job-runner **не вводятся**.
- **Обязательная доработка:** `ProjectIdlePauseService._last_activity_at`
  (`idle_pause.py:25-40`) считает idle только по agent-сессиям и не видит активные
  job'ы. Добавляется реестр активных `job_id` с lease, иначе backend уснёт посреди
  xlsx-экспорта (acceptance №14 в v3 не был реализуем).
- Wake-up по событию делает Plugin Gateway при публикации (он владеет lifecycle).

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

### 7.4 Засыпание: отдельный activator, НЕ SandboxClaim

**Замена v3.** `SandboxClaim` — рабочая модель для агентов, но для плагинов она
неприменима категориально (проверено по CRD и `runtime.py`):

1. CRD `SandboxClaim` не имеет `image`, `containerPort`, `command`;
   `warmPoolRef` обязателен; `envVarsInjectionPolicy: Disallowed` — нельзя передать
   даже `TENANT_DB_DSN`/`PLUGIN_ID`, которые требуются §5.1.
2. Один `SandboxTemplate` на WarmPool; пул греет `prodavan-agent-runtime:3921`,
   а не образ плагина. N плагинов × M версий = N×M пулов × 2 пода against квоты
   45 подов.
3. Stateful по дизайну: PVC 5Gi workspace + `Suspended` сохраняет volume. Плагин
   stateless (состояние в `tenant_<company>`), PVC не нужен, а local-path RWO мешает
   рестарту.
4. `ensure_running` **не ждёт Ready** (`runtime.py:336-348`): gateway получит
   connection-refused на первый запрос, SSE/долгий webhook оборвётся.

**Решение: собственный activator на базе уже запроектированного Plugin Gateway.**

| Аспект | Реализация v4 |
|---|---|
| Workload | обычный `Deployment` плагина с `replicas: 0`, `readinessProbe /health`, `imagePullPolicy: IfNotPresent`, явные `resources` |
| Пробуждение | Gateway патчит `deployments/scale` (RBAC `Role`/`RoleBinding` уже в whitelist) и **удерживает соединение до Ready**, затем проксирует |
| Idle | TTL / `last-active` в БД или ConfigMap, ведёт Gateway; при активном `job_id` усыпление запрещено |
| Холодный старт | доминирует pull образа (секунды/десятки секунд, не 100–500 мс); митигация — предпрогрев кэша образа на единственной ноде при установке плагина и лимит одновременных wake-up |
| GitOps | `ignoreDifferences: [{group: apps, kind: Deployment, jsonPointers: [/spec/replicas]}]` в Argo Application. **Никаких новых CRD** |
| Агенты | остаются на `SandboxClaim` без изменений |

Общим остаётся **контракт** (`ensure_running` / `pause` / idle TTL / единая схема
аудита `plugin_wake_up`), а не реализация. «Один lifecycle-контроллер на всех» из
v3 был ложной унификацией: агенты stateful+файлы, плагины stateless+HTTP.

**Почему не KEDA/Knative:** KEDA HTTP Add-on — достойная цель на будущее
(0→1 по HTTP, [документация](https://raw.githubusercontent.com/kedacore/http-add-on/main/README.md),
сейчас beta), но требует `keda.sh/*`, `http.keda.sh/*`, `autoscaling/HPA` в
`00-project.yaml` и +4–6 подов control plane. Knative — +8–12 подов и второй
ingress-стек. На single-node k3s с 12Gi requests и тремя OOM-инцидентами оба
несоразмерны. Решение фиксируется в ADR-008.

Wake-up триггеры: HTTP (iframe, webhook, pluginpage), cron перед срабатыванием,
событие до доставки, явный `POST /gateway/plugins/wake` → `202` + `Retry-After`.

---

## 8. Путь миграции: цель «под нож», метод — strangler

Разрешаем противоречие v1: **«всё под нож» — про конечное состояние, а не про способ перехода.** В финале declarative-app (`module_meta_documents`, generic runtime, `module_instance_data_rows`, `ModuleRuntime.tsx`) удалён полностью. Параллельный прогон нужен **только из-за объёма кода**, а не из-за данных:

- `mod_equipment` — ~13k строк: 18 Python-файлов (9 678 строк) + 16 TS/TSX (3 485 строк), отдельная страница `/equipment`, Celery beat/sweep, MCP-сервер, OpenSearch-индексы.
- Это **dev-разработка**: данные эфемерны, бэкапы не нужны, перенос данных не нужен, DOWN-миграции платформы не требуются. Любые тестовые данные создаются заново сидом.

**Этапы:**

1. **Инфраструктура** — tenant PostgreSQL (отдельный Deployment/Service/Secret), роли/БД при grant, PgBouncer, `pgaudit`, **PluginClaims + `/capabilities/v1/*`** (а не расширение `:8001`), OCI/cosign в CI, bucket-per-tenant + STS, Redpanda dispatch, egress-генератор, Ingress priority 120, Plugin Gateway + activator, `ignoreDifferences`. Declarative-app работает как прежде.
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
| DoS через egress | `none`/`webhooks-only`/`full`, allowlist из **`CapabilityGrant` в БД** (не из краткоживущего JWT), квота байт, аудит |
| `/webhook/` уезжает в SPA | `router.priority: 120` + entrypoints + ingressClassName + тест приоритетов |
| Сломанный cutover | strangler + фича-флаг; данные dev эфемерны, откат = выключить флаг |
| Смешение agent/plugin trust | отдельные `typ/aud/kid`, отдельная ветка allowlist middleware, `:8001` агентам без изменений |
| Один «шумный» плагин исчерпал квоту компании | `CompanyTenantInfraQuota` расширен до per-`(company, plugin_instance, artifact_digest)` |
| Утечка объектов через баг в тегах | IAM/bucket-граница вместо Python-фильтра; теги — только метаданные |
| Потеря событий при простое консьюмера | Redpanda dispatch: ручной commit, `earliest`, DLQ, вместо `kick`+`auto_commit` |
| Backend уснул посреди активного job | реестр активных `job_id` с lease в idle-pause |
| Неподписанный backend-образ | один OCI digest + cosign verify до деплоя, pin по digest, `:latest` запрещён |
| Argo selfHeal вернул `replicas: 0` | `ignoreDifferences /spec/replicas` в Argo Application (CRD не нужен) |
| Расхождение политик | один генератор NP, одна схема egress-аудита, один реестр capability, общий контракт lifecycle (реализации разделены) |

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
| 12 | `revoke` деактивирует `CapabilityGrant`; старый `PluginClaims` отклоняется по `grant_id` | integration gateway |
| 13 | Wake-up из `replicas: 0` удерживает запрос до Ready и выдаёт новый короткий токен | e2e activator + `ignoreDifferences` |
| 14 | Активный `job_id` блокирует усыпление backend | integration lifecycle + реестр активных job'ов |
| 15 | Uninstall дропает схему/роль и bucket/prefix, не трогая чужие БД | integration lifecycle |
| 16 | Фича-флаг `plugin_enabled_modules[]` переключает путь без миграции данных | e2e: старый/новый путь работают независимо |
| 17 | Плагин с `PluginClaims` не имеет доступа к agent-surface `:8001` (`/agent/*`, `/internal/pods/*`) | e2e: `403` на всех agent-путях |
| 18 | `cosign verify` до деплоя; неподписанный/несовпавший digest → `QUARANTINED`, деплой по digest, не `:latest` | CI gate + unit реестра |
| 19 | STS creds плагина не видят объекты чужого bucket/префикса | integration MinIO policy |
| 20 | Redpanda consumer не теряет событие при простое (ручной commit, `earliest`, DLQ) | integration топика `agentscale.plugin.events` |
| 21 | Один плагин не может исчерпать квоту компании | unit per-`(company, instance, digest)` |
| 22 | Egress-политика не зависит от срока жизни JWT | integration: истёкший токен не меняет allowlist из grant |

---

## 12. Открытые вопросы

1. **Публичные страницы** — какие данные допустимы без сессии; формат anon-scope.
2. **Cross-module sharing** — пока запрещено; если понадобится, только через явный grant в gateway.
3. **Маркетплейс** — публикация/установка плагинов, отдельный документ.
4. **Billing** — привязка `quota_ref` к тарифу.
5. **ADR-008** — разделение lifecycle агентов/плагинов и выбор собственного activator (обязателен до реализации §7.4).
6. **Переход на wildcard subdomain** — условия в §5.3 (публичный домен, cert-manager DNS-01, whitelist kinds).
7. **KEDA HTTP Add-on** — целевой вариант после снятия ограничений whitelist/бюджета; в v4 не вводится.
8. **MinIO STS** — подтвердить официальной документацией `AssumeRole` / policy variables (в этом ревью страница была недоступна); при отсутствии STS — scoped presigned с policy variables.

Закрытые вопросы: доступ к БД (B, прямой DSN); реестр артефактов (OCI + cosign); изоляция объектов (bucket/STS вместо тегов); identity (`PluginClaims` вместо Bridge JWT); lifecycle (собственный activator вместо `SandboxClaim`); бэкапы/перенос/dual-read — исключены (dev, данные эфемерны); ADR-007 принят.

---

## 13. Что отвергнуто и почему

### 13.1 Ошибки v1 (неработающие механизмы)

| Было | Почему отвергнуто |
|---|---|
| RLS как граница | `ENABLE` без `FORCE` не действует на owner; owner = плагин; `app.*` GUC пере-set'ится |
| `CONNECTION LIMIT` как размер БД | это лимит сессий |
| `statement_timeout` как rate limit | это таймаут statement, переопределяется в сессии |
| `REVOKE SET ON PARAMETER` как защита | осмысленна только для superuser-only параметров |
| `POST /adapters/db/query {"sql": ...}` | запрещено ADR-002-capability-gateway (generic row dispatch) |
| Knative scale-to-zero / cold start 100–500 мс | нет CRD в whitelist; на этом кластере pull образов — секунды/десятки секунд |
| `sandbox="allow-scripts allow-same-origin"` | classic sandbox bypass |
| `companies.tenant_db_connection` | единая точка компрометации; учётные данные в k8s Secret на компанию |
| `v1→v4 deprecate` при «всё под нож» | разведено: цель = удаление, метод = strangler |
| Дубли §9.2/§9.6, §9.3/§9.7, два `## 10.`, два ADR-002 | удалены; новый → ADR-006, добавлен реестр |
| Токен в URL `?token=...`, клиентские заголовки после доверенных, анонимный `pluginpage_proxy`, статический Envoy-кластер, просроченный `exp`, самореференция digest | исправлены в v2 |
| `TenantUserDbService._ALLOWED_TYPES`, изолированный Liquibase Job под доверенной ролью, парсер запрещённых SQL | v2/v3: это та же ограниченная декларативность; плагин владеет схемой и мигрирует сам |
| `migrate-to-plugin`, dual-read/dual-write, snapshot, бэкапы, метрики парности | не нужны: dev-разработка, данные эфемерны |

### 13.2 Что **принято** в v3 (вопреки v2)

| Решение | Комментарий |
|---|---|
| `CREATE DATABASE tenant_<company_id>` | при прямом DSN это настоящая граница изоляции; один кластер + PgBouncer |
| Прямой `TENANT_DB_DSN` плагину | ADR-007 supersede ADR-003 в части `db.direct` |
| Egress плагина на tenant-postgres :5432 | `prodavan` по-прежнему недостижим |
| `CREATE DATABASE` на компанию, реальные таблицы, self-managed Liquibase | полноценное приложение вместо структурированного манифеста |

### 13.3 Что **заменено** в v4 (главная правка: «as-built → не значит как есть»)

| Было в v2/v3 | Стало | Причина |
|---|---|---|
| `PodBridgeClaims` как identity плагина, расширение pod-gateway `:8001` | `PluginClaims` + отдельный route-group Plugin Gateway; `:8001` только агентам | разные trust-модели; иначе плагин получает `/agent/*` и `/internal/pods/*` |
| Один MinIO-бакет + tenant-теги, «presigned намеренно отвергнуты» | bucket-per-tenant / STS session policy + scoped presigned; теги — метаданные | теги — Python-фильтр, не IAM; один service user на `agentscale/*` |
| Celery-outbox как шина событий | Redpanda dispatch (ручной commit, `earliest`, DLQ) + outbox только для webhook-доставки | `kick` + `auto_commit` теряет события при простое консьюмера |
| `tar.gz + Ed25519 + свой реестр ключей` | OCI в GHCR + cosign keyless + SBOM/provenance + `AppVersion`/`RegistryOutcome` | подписанный manifest ссылался на неподписанный образ; CI уже имеет ghcr, но отключает provenance |
| `SandboxClaim` + warm pool для плагинов | Deployment `replicas: 0` + Plugin Gateway activator + `ignoreDifferences` | CRD не несёт image/port, запрещает env, тащит PVC, не ждёт Ready; пул греет не тот образ |
| «Единая NP для плагинов и агентов» | две NP из одного генератора; общая схема аудита | у плагинов 5432/:9000, у агентов — нет |
| Egress/cluster rules из capability token | из `CapabilityGrant` в БД; токен несёт `grant_id` | иначе истечение JWT пересобирает сетевую политику |
| path-based как окончательный ответ | v1 static-origin, цель — wildcard subdomain при выполнении §5.3 | §5.2 требовал отдельный origin, §7.1 сажал на тот же host |
| «Один lifecycle-контроллер на всех» | общий контракт, разные реализации | агенты stateful+файлы, плагины stateless+HTTP |

---

## 14. Порядок реализации

1. ADR-реестр, ADR-006, ADR-007, **ADR-008** (разделение lifecycle агентов/плагинов).
2. Tenant PostgreSQL: отдельный Deployment/Service/Secret, PgBouncer, `pgaudit`.
3. `PluginClaims` + `/capabilities/v1/*` в Plugin Gateway + ветка allowlist middleware; `:8001` не трогаем. `CapabilityGrant` с per-`(company, instance, digest)` квотами.
4. Lifecycle grant/revoke/uninstall: `CREATE DATABASE`, роль, схема, `search_path`, `CONNECT`; bucket-per-tenant + STS/presigned.
5. CI: `provenance`/`sbom` = true, cosign keyless, pin по digest; реестр на `AppVersion`/`RegistryOutcome`.
6. Egress-генератор (правила из grant в БД) + две NP + единая схема аудита + CSP/static-origin.
7. Plugin Gateway + Ingress (`router.priority: 120`) + тест приоритетов + webhook-контракт.
8. Redpanda dispatch (`agentscale.plugin.events`, ручной commit/DLQ/earliest) + реестр активных `job_id` в idle-pause.
9. Activator: `Deployment replicas: 0` + wake/hold-until-Ready + `ignoreDifferences /spec/replicas`; агенты остаются на `SandboxClaim`.
10. Пилотный плагин за фича-флагом `plugin_enabled_modules[]` с собственными таблицами, Liquibase и тестовыми данными.
11. Strangler по контекстам `mod_equipment` → cutover → удаление declarative-app.

---

**Конец документа.** Дизайн, не код. Реализация через субагентов на main; микрокоммиты `git add -A && git commit && git push`, без гигиены и без переписывания истории; тесты пишутся, но не запускаются до явного решения. Блокирующие условия — принятие ADR-008 и подтверждение MinIO STS по официальной документации (или переход на scoped presigned).
