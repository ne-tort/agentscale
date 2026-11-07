# Plugin Architecture — Полный дизайн (v1)

**Дата:** 2026-11-07  
**Статус:** Целевая модель. Реализация — отдельная миграция.  
**Принцип:** "Все под нож, жестко" — declarative-app deprecated, plugin-modules — единственный путь.

---

## 0. Цели и ограничения

### 0.1 Цели

1. Плагин — **полноценное приложение** (веб + бэкенд), а не набор JSON-описаний экранов.
2. Плагин имеет **прямой доступ к своей БД** (PostgreSQL), может создавать таблицы, мигрировать схему.
3. Доступ к инфраструктуре — **только через capability adapters** (универсальные контракты, не зависящие от реализации).
4. **Изоляция**:
   - От других плагинов (своя БД, свой bucket, свой namespace)
   - От внешних сервисов (регулируемый egress: none / webhooks-only / full)
   - От shared инфраструктуры (ACL + quotas + audit)
5. **Универсальный firewall** для плагинов **и** для контейнеров AI-агентов (единая модель).
6. Плагин загружается **динамически** (не на этапе сборки основного приложения).

### 0.2 Ограничения (не в v1)

- WASM sandbox для плагинов (пока iframe / isolated origin)
- Полностью serverless плагины (пока контейнеры или sidecar)
- Кросс-tenant sharing данных между плагинами (пока запрещено)

---

## 1. Tenant & Data Isolation Model

### 1.1 Иерархия

```
Platform (shared)
├── PostgreSQL cluster (1+ экземпляров)
│   ├── `prodavan` (основная БД приложения)
│   │   ├── `companies`, `cabinets`, `projects`, `employees`, ...
│   │   └── `module_grants`, `module_bindings`, ...
│   └── `tenant_<company_id>` (динамически создаваемая БД на компанию)
│       ├── `module_<module_id>_<plugin_version>` (схема плагина)
│       │   ├── таблицы плагина (создаются миграциями плагина)
│       │   └── `plugin_migrations` (таблица версий миграций)
│       ├── `module_<module_id>_shared` (общие данные плагина для всех кабинетов компании)
│       └── `module_<module_id>_cabinet_<cabinet_id>` (данные на кабинет, если нужно)
│
├── MinIO
│   ├── `prodavan-shared` (артефакты приложения)
│   └── `tenant-<company_id>` (данные компании)
│       ├── `module-<module_id>/` (данные плагина)
│       └── `projects/<project_id>/` (workspace проекта)
│
├── Redis (tenant-aware key prefixes)
│   ├── `tenant:<company_id>:module:<module_id>:*`
│   └── `tenant:<company_id>:project:<project_id>:*`
│
└── OpenSearch / MongoDB / RabbitMQ / Celery (tenant-aware)
    └── `tenant:<company_id>:module:<module_id>:*`
```

### 1.2 Правила именования

- БД компании: `tenant_<company_id>` (UUID без дефисов, lowercase)
- Схема плагина: `module_<module_id>_<plugin_version>` (например, `module_mod_equipment_v1_0_0`)
- Bucket MinIO: `tenant-<company_id>` (дефисы вместо подчёркиваний для совместимости с DNS)
- Redis keys: `tenant:<company_id>:module:<module_id>:<key>`

### 1.3 Создание tenant DB

При создании компании (`POST /admin/companies`):
1. Создаётся БД `tenant_<company_id>` (через `CREATE DATABASE` с `TEMPLATE template0`)
2. Создаётся роль `tenant_<company_id>_app` с ограниченными правами
3. Применяются базовые миграции (таблица `plugin_registry`, `module_grants` и т.д.)
4. Роль и connection string сохраняются в `companies.tenant_db_connection` (зашифровано)

При удалении компании:
1. БД `tenant_<company_id>` **архивируется** (переименовывается в `tenant_<company_id>_archived_<timestamp>`)
2. Через N дней (policy) — `DROP DATABASE`
3. Audit: `tenant_db_archived`, `tenant_db_dropped`

---

## 2. Database Architecture (PostgreSQL)

### 2.1 Прямой доступ vs Adapter

**Прямой доступ** (для таблиц плагина):
- Плагин подключается к `tenant_<company_id>` как `tenant_<company_id>_plugin_<module_id>`
- Роль создаётся при grant/bind модуля
- Роль имеет `USAGE` на схему `module_<module_id>_<version>`
- Роль имеет `SELECT/INSERT/UPDATE/DELETE` на таблицы схемы
- **Row-Level Security (RLS)**: каждая таблица плагина имеет политику `tenant_id = current_setting('app.tenant_id')`

**Adapter** (для shared инфраструктуры):
- Плагин **не имеет прямого доступа** к `prodavan` БД, Redis, OpenSearch и т.д.
- Все обращения идут через HTTP/gRPC адаптеры (см. §4)

### 2.2 Миграции плагина

Плагин сам управляет своими миграциями:

```sql
-- Таблица в схеме плагина (создаётся при install)
CREATE TABLE module_<module_id>_<version>.plugin_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum TEXT NOT NULL,
    description TEXT
);

-- Плагин вызывает endpoint адаптера:
POST /adapters/db/migrations/apply
{
  "module_id": "mod_equipment",
  "version": "1.2.0",
  "migrations": [
    {"version": "0001", "sql": "CREATE TABLE ..."},
    {"version": "0002", "sql": "ALTER TABLE ..."}
  ],
  "checksum": "sha256:..."
}
```

Адаптер:
1. Проверяет, что версия не применена (`plugin_migrations`)
2. Применяет миграции в транзакции
3. Записывает в `plugin_migrations`
4. Audit: `plugin_migration_applied`

**Ограничения**:
- Плагин может создавать/изменять таблицы **только в своей схеме**
- Плагин **не может** создавать/удалять БД, роли, схемы вне своей
- Плагин **не может** `GRANT/REVOKE` на shared таблицы

### 2.3 PostgreSQL возможности ограничения

| Возможность | Как реализовано | Примечание |
|-------------|-----------------|------------|
| **Размер БД** | `CREATE DATABASE ... WITH CONNECTION LIMIT` + `ALTER ROLE ... SET statement_timeout` | Нет встроенного `max_size`, мониторим через `pg_database_size()` + alert |
| **Rate limit** | `ALTER ROLE ... SET statement_timeout = '5s'`; `pg_stat_statements` + `pg_cron` для throttling | На уровне роли/сессии |
| **Auth** | `tenant_<company_id>_plugin_<module_id>` роль с `PASSWORD` (rotation при reinstall) | SCRAM-SHA-256 |
| **RLS** | `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`; `CREATE POLICY tenant_isolation ON ... USING (tenant_id = current_setting('app.tenant_id'))` | Обязательно для всех таблиц плагина |
| **Connection pooling** | PgBouncer (tenant-aware pool) | Отдельный pool на tenant DB |

### 2.4 Tenant-aware connection

При подключении плагина:
```python
# В capability DB adapter
conn = await psycopg.connect(
    dsn=tenant_dsn,
    options="-c app.tenant_id=co_123 -c app.module_id=mod_equipment"
)
await conn.execute("SET app.tenant_id = %s", (company_id,))
await conn.execute("SET app.module_id = %s", (module_id,))
```

RLS политика:
```sql
CREATE POLICY tenant_isolation ON equipment_offers
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

---

## 3. Object Storage (MinIO)

### 3.1 Bucket strategy

```
MinIO
├── prodavan-shared/
│   ├── artifacts/          # Общие артефакты (Docker images, seeds и т.д.)
│   └── templates/          # Шаблоны документов
│
└── tenant-<company_id>/
    ├── module-<module_id>/           # Данные плагина (файлы, экспорты)
    │   ├── exports/
    │   ├── uploads/
    │   └── cache/
    ├── projects/<project_id>/        # Workspace проекта (MinIO mirror)
    │   ├── workspace/
    │   └── inbox/
    └── module-<module_id>-shared/    # Общие данные плагина для всех кабинетов компании
```

### 3.2 ACL

- Плагин получает **signed URL** через адаптер (`POST /adapters/objects/sign`)
- Подпись ограничена по времени (TTL) и по path prefix (`module-<module_id>/*`)
- Плагин **не имеет** `s3:*` прав на bucket; только через адаптер

### 3.3 Quotas

- `max_object_size` (per object)
- `max_objects` (per module)
- `max_total_size` (per tenant DB)

---

## 4. Capability Adapters (контракты)

### 4.1 Общие принципы

- Адаптеры — **HTTP/gRPC сервисы** (не библиотеки)
- Плагин общается с адаптером по **универсальному контракту** (OpenAPI/gRPC)
- Адаптер реализует ACL, quotas, audit, tenant isolation
- Плагин **не знает**, что за реализация (PostgreSQL, Redis, OpenSearch, RabbitMQ и т.д.)

### 4.2 Capability Token

При bind модуля к проекту плагин получает **capability token** (JWT):

```json
{
  "iss": "prodavan",
  "sub": "module:mod_equipment:instance:inst_abc123",
  "tenant": "co_123",
  "project": "proj_456",
  "capabilities": [
    "db:read:equipment_offers",
    "db:write:equipment_offers",
    "objects:sign:module-mod_equipment",
    "pods:invoke:equipment.pipeline",
    "mcp:register:prodavan-equipment"
  ],
  "quotas": {
    "db:max_rows": 100000,
    "objects:max_size": "100MB"
  },
  "exp": 1762540800
}
```

Токен передаётся в заголовке `Authorization: Bearer <token>` при каждом обращении к адаптеру.

### 4.3 DB Adapter (контракт)

**Endpoint:** `POST /adapters/db/query`

```json
// Request
{
  "sql": "SELECT * FROM equipment_offers WHERE group_id = $1 LIMIT $2",
  "params": ["grp_123", 100],
  "timeout_ms": 5000
}

// Response
{
  "rows": [...],
  "row_count": 42,
  "execution_time_ms": 12
}
```

**Endpoint:** `POST /adapters/db/execute` (для INSERT/UPDATE/DELETE)

**Endpoint:** `POST /adapters/db/migrations/apply` (см. §2.2)

**Ограничения**:
- Только на таблицы в своей схеме (`module_<module_id>_<version>.*`)
- RLS автоматически применяется через `SET app.tenant_id`
- Rate limit: `max_queries_per_minute`, `max_query_time`

### 4.4 Redis Adapter (контракт)

**Endpoint:** `POST /adapters/redis/command`

```json
// Request
{
  "command": "SET",
  "args": ["module:mod_equipment:cache:offer:123", "value", "EX", 3600]
}

// Response
{"result": "OK"}
```

**Поддерживаемые команды:** `GET`, `SET`, `DEL`, `INCR`, `EXPIRE`, `LPUSH`, `RPOP`, `PUBLISH`, `SUBSCRIBE` (ограниченный набор)

**Ограничения**:
- Key prefix: `tenant:<company_id>:module:<module_id>:*`
- Rate limit: `max_commands_per_minute`, `max_memory`

### 4.5 Objects Adapter (контракт)

**Endpoint:** `POST /adapters/objects/sign`

```json
// Request
{
  "bucket": "tenant-co_123",
  "key": "module-mod_equipment/exports/report-123.xlsx",
  "method": "GET",
  "ttl_seconds": 3600
}

// Response
{
  "url": "https://minio.example.com/tenant-co_123/module-mod_equipment/exports/report-123.xlsx?X-Amz-...",
  "expires_at": "2026-11-08T00:00:00Z"
}
```

**Endpoint:** `POST /adapters/objects/upload-url` (presigned PUT)

### 4.6 Pods Adapter (контракт)

**Endpoint:** `POST /adapters/pods/invoke`

```json
// Request
{
  "action": "equipment.pipeline",
  "params": {"group_id": "grp_123"},
  "timeout_seconds": 300
}

// Response
{
  "job_id": "job_abc123",
  "status": "running"
}
```

Плагин **не имеет** прямого доступа к k8s API. Только через адаптер.

### 4.7 MCP Adapter (контракт)

**Endpoint:** `POST /adapters/mcp/register`

```json
// Request
{
  "module_id": "mod_equipment",
  "tools": [
    {"name": "equipment.search", "description": "...", "input_schema": {...}}
  ]
}

// Response
{"registered": true}
```

### 4.8 Audit

Каждое обращение к адаптеру логируется:
- `plugin_db_query` (sql, params, row_count, execution_time)
- `plugin_redis_command` (command, key, ttl)
- `plugin_object_sign` (bucket, key, method, ttl)
- `plugin_pod_invoke` (action, params, job_id)
- `plugin_mcp_register` (tools)

---

## 5. Plugin Runtime (загрузка кода)

### 5.1 Два режима

| Режим | Описание | Когда |
|-------|----------|-------|
| **declarative-app** | JSON-описание экранов + generic runtime | Простые CRUD-модули (устаревает) |
| **code-plugin** | Полноценный bundle (веб + бэкенд), загружаемый в iframe | Сложные модули (цель) |

### 5.2 Code-plugin: структура артефакта

```
plugin-mod_equipment-v1.2.0.tar.gz
├── manifest.json
│   {
│     "plugin_id": "mod_equipment",
│     "version": "1.2.0",
│     "artifact_digest": "sha256:...",
│     "frontend": {"entry": "index.html", "base_path": "/plugins/mod_equipment/"},
│     "backend": {"entry": "server.py", "port": 8080, "healthcheck": "/health"},
│     "capabilities": ["db", "objects", "pods", "mcp"],
│     "egress": {"mode": "webhooks-only", "allowed_paths": ["/webhook/*"]}
│   }
├── frontend/
│   ├── index.html
│   ├── assets/
│   └── ...
├── backend/
│   ├── server.py
│   ├── migrations/
│   │   ├── 0001_init.sql
│   │   └── 0002_add_indexes.sql
│   └── ...
└── metadata/
    └── icon.svg
```

### 5.3 Загрузка

1. При grant/bind модуля к проекту:
   - Скачивается артефакт из реестра (по `artifact_digest`)
   - Проверяется подпись (Ed25519)
   - Развёртывается:
     - Frontend: статические файлы в MinIO `tenant-<company_id>/plugins/<module_id>/`
     - Backend: контейнер (или sidecar) с переменными окружения (capability token, tenant DB DSN, adapter URLs)
2. Shell рендерит:
   ```html
   <iframe
     src="https://plugins.agentscale.dev/tenant-co_123/mod_equipment/index.html?token=..."
     sandbox="allow-scripts allow-same-origin allow-forms"
   />
   ```

### 5.4 Backend плагина

Backend плагина — **отдельный процесс** (контейнер или sidecar), который:
- Слушает порт (например, 8080)
- Получает capability token из env (`PLUGIN_CAPABILITY_TOKEN`)
- Общается с адаптерами через HTTP (`ADAPTER_DB_URL`, `ADAPTER_REDIS_URL` и т.д.)
- Имеет прямой доступ к своей БД (`TENANT_DB_DSN`)

Пример переменных окружения:
```bash
PLUGIN_ID=mod_equipment
PLUGIN_VERSION=1.2.0
PLUGIN_CAPABILITY_TOKEN=eyJhbGciOi...
TENANT_DB_DSN=postgresql://tenant_co_123_plugin_mod_equipment:***@pg/tenant_co_123
ADAPTER_DB_URL=http://capability-adapters:9000/adapters/db
ADAPTER_REDIS_URL=http://capability-adapters:9000/adapters/redis
ADAPTER_OBJECTS_URL=http://capability-adapters:9000/adapters/objects
ADAPTER_PODS_URL=http://capability-adapters:9000/adapters/pods
EGRESS_MODE=webhooks-only
EGRESS_PROXY_URL=http://egress-proxy:8080
```

### 5.5 Frontend плагина

Frontend плагина — **статический bundle**, который:
- Загружается из MinIO
- Общается с backend плагина через `postMessage` (или через capability token в заголовках)
- Не имеет прямого доступа к shared API (только через backend плагина)

---

## 6. Network Isolation & Firewall

### 6.1 Универсальная модель

**Единая система фаерволла** для:
- Plugin containers (code-plugin backend)
- AI agent containers (Pod workspace)

**Принцип:** NetworkPolicy (k8s) + Egress Proxy (Envoy / custom) + Capability-based egress rules.

### 6.2 NetworkPolicy (k8s)

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: plugin-mod_equipment
  namespace: agentscale-dev-sandboxes
spec:
  podSelector:
    matchLabels:
      app: plugin
      module_id: mod_equipment
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              name: agentscale-dev
          podSelector:
            matchLabels:
              app: capability-adapters
      ports:
        - protocol: TCP
          port: 9000  # adapters
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              name: agentscale-dev
          podSelector:
            matchLabels:
              app: capability-adapters
      ports:
        - protocol: TCP
          port: 9000
    - to:
        - namespaceSelector:
            matchLabels:
              name: agentscale-dev
          podSelector:
            matchLabels:
              app: egress-proxy
      ports:
        - protocol: TCP
          port: 8080
    # Egress to tenant DB (только для DB adapter)
    - to:
        - podSelector:
            matchLabels:
              app: postgresql
      ports:
        - protocol: TCP
          port: 5432
```

### 6.3 Egress Proxy (Envoy)

**Режимы egress:**

| Режим | Описание | Пример |
|-------|----------|--------|
| `none` | Полная изоляция (нет egress) | Внутренние модули |
| `webhooks-only` | Только webhook URLs (из allowlist) | Интеграции с внешними системами |
| `full` | Прямой egress (с логированием) | Внешние API (с approval) |

**Envoy config (webhooks-only):**
```yaml
static_resources:
  listeners:
    - address: {socket_address: {address: 0.0.0.0, port_value: 8080}}
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                stat_prefix: egress
                route_config:
                  name: local_route
                  virtual_hosts:
                    - name: backend
                      domains: ["*"]
                      routes:
                        - match: {prefix: "/webhook/"}
                          route: {cluster: external_webhooks}
                        - match: {prefix: "/"}
                          direct_response: {status: 403, body: "Egress denied"}
  clusters:
    - name: external_webhooks
      type: STRICT_DNS
      load_assignment:
        cluster_name: external_webhooks
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: webhooks.example.com
                      port_value: 443
```

**Egress rules хранятся в capability token:**
```json
{
  "egress": {
    "mode": "webhooks-only",
    "allowed_hosts": ["webhooks.example.com"],
    "allowed_paths": ["/webhook/*"]
  }
}
```

### 6.4 Egress Audit

Все egress запросы логируются:
- `plugin_egress_request` (host, path, method, status, bytes)
- `agent_egress_request` (тот же формат, для AI agent containers)

---

## 7. Миграция от declarative-app

### 7.1 Deprecation path

1. **v1 (текущий):** declarative-app — основной путь
2. **v2:** capability adapters + code-plugin (пилот на `mod_equipment`)
3. **v3:** declarative-app deprecated (только для простых CRUD)
4. **v4:** declarative-app removed

### 7.2 Миграция данных

- declarative-app данные (`module_instance_data_rows`) остаются в shared БД
- plugin-app данные (`module_<module_id>_<version>.*`) в tenant DB
- Migration tool: `POST /admin/modules/{id}/migrate-to-plugin` (копирует данные, применяет миграции плагина)

### 7.3 UI migration

- Старые views (declarative-app) рендерятся через legacy adapter (deprecated)
- Новые views (plugin-app) рендерятся через iframe

---

## 8. Типовые паттерны

### 8.1 Плагин с БД

```python
# backend/server.py
from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, text

app = FastAPI()
engine = create_engine(os.environ["TENANT_DB_DSN"])

@app.on_event("startup")
async def startup():
    # Применить миграции (если не применены)
    await apply_migrations(engine)

@app.get("/offers")
async def list_offers(token: str = Depends(get_capability_token)):
    # Проверка capability
    if "db:read:equipment_offers" not in token.capabilities:
        raise HTTPException(403)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM equipment_offers LIMIT 100"))
        return result.fetchall()
```

### 8.2 Плагин с webhook

```yaml
# manifest.json
{
  "egress": {
    "mode": "webhooks-only",
    "allowed_hosts": ["hooks.slack.com"],
    "allowed_paths": ["/services/*"]
  }
}
```

```python
# backend/server.py
@app.post("/notify")
async def notify(body: dict, token: str = Depends(get_capability_token)):
    if token.egress.mode != "webhooks-only":
        raise HTTPException(403)
    # Проксирование через egress proxy
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://egress-proxy:8080/webhook/slack",
            json=body,
            headers={"X-Plugin-ID": token.sub}
        )
        return response.json()
```

### 8.3 Плагин с MCP tool

```python
# backend/server.py
@app.post("/mcp/register")
async def register_mcp(token: str = Depends(get_capability_token)):
    await adapter_mcp.register(
        module_id=token.sub,
        tools=[{"name": "equipment.search", ...}]
    )
```

---

## 9. Open Questions (требуют решения)

1. ** versioning plugin DB schema** — как плагин узнаёт, какая версия схемы уже применена? (таблица `plugin_migrations` — OK, но нужно версионирование самого плагина)
2. **Rollback миграций** — поддерживать ли `DOWN` миграции? (сложно, пока не поддерживаем)
3. **Cross-module sharing** — может ли плагин A читать данные плагина B? (пока запрещено, но может потребоваться)
4. **Plugin marketplace** — как публиковать/устанавливать плагины из реестра? (отдельный документ)
5. **Billing / quotas enforcement** — как enforced quotas (DB size, egress bytes)? (pg_cron + alerts vs hard limit)
6. **Plugin secrets** — как плагин хранит свои секреты (API keys, DB passwords)? (отдельный адаптер `secrets`?)

---

## 10. Следующие шаги (дизайн)

1. **Capability Token format** (JWT claims, rotation)
2. **DB Adapter OpenAPI spec** (полный контракт)
3. **Egress Proxy config** (Envoy + custom controller)
4. **Plugin manifest schema** (JSON Schema)
5. **Tenant DB lifecycle** (CREATE DATABASE, role management, archiving)
6. **Audit event schema** (все `plugin_*` события)

---

**Конец документа.** Всё, что выше — **дизайн**, не код. Реализация через субагентов после утверждения.
