# 04 — API и инфраструктурный доступ

## Контекст

Две поверхности:

1. **External API** — UI/оператор: projects, modules, agent sessions, chat, cabinets, admin.
2. **Pod / agent surface** — Bridge JWT: hydrate, credentials lease, tenant infra (cache/docs/userdb/events/objects/search), module rows/actions/meta, agent event append.

Цель: агент в контейнере получает доступ к инфраструктуре приложения **через API**, с ACL и квотами — не прямым DSN в Pod к shared Mongo/OS/Kafka платформы.

## Текущая реализация (as-is)

### External API

- App: [`main.py`](../../../apps/api/src/prodavan/main.py) — allowlist middleware **до** CORS; `/api/v1` router.
- Auth: [`api/deps.py`](../../../apps/api/src/prodavan/api/deps.py) — Bearer JWT → `Principal`; `X-Cabinet-Id` / `X-Project-Id`.
- Agent routes: [`api/v1/agent.py`](../../../apps/api/src/prodavan/api/v1/agent.py) — sessions, send, chat/stream, transcript, approvals, fork, append events (bridge).
- Rate limit: Redis fixed-window [`api/rate_limit.py`](../../../apps/api/src/prodavan/api/rate_limit.py).

### Pod auth & allowlist

- [`api/agent_auth.py`](../../../apps/api/src/prodavan/api/agent_auth.py) — preferred Bridge JWT; **shared Bearer rejected** на pod surface.
- [`core/middleware.py`](../../../apps/api/src/prodavan/core/middleware.py) — `PodSurfaceAllowlistMiddleware`; regex допускает `internal/pods`, `projects/.../infra|modules|agent`.
- Bridge claims: [`pod_identity/bridge.py`](../../../apps/api/src/prodavan/application/pod_identity/bridge.py) — scopes, HS256, `_signing_secret` с цепочкой fallback → `dev-pod-bridge-secret`.

### Internal Pod API

[`api/internal/pods.py`](../../../apps/api/src/prodavan/api/internal/pods.py):

- `GET .../workspace-archive` — tar в память ([`workspace_tar_download.py`](../../../apps/api/src/prodavan/application/pod_service/workspace_tar_download.py), cap 512 MB).
- Credentials via [`credential_broker.py`](../../../apps/api/src/prodavan/application/agent/credential_broker.py) — lease + one-time secret.

Pod env: [`pod_spec.py`](../../../apps/api/src/prodavan/infrastructure/k8s/sandbox/pod_spec.py) — может инжектить `PRODAVAN_AUTH_TOKEN` **literal** + `BRIDGE_AUTH_TOKEN` secretRef.

### Tenant infra

[`application/tenant_infra/`](../../../apps/api/src/prodavan/application/tenant_infra/) + [`api/v1/tenant_infra.py`](../../../apps/api/src/prodavan/api/v1/tenant_infra.py):

- Плоскости: cache, docs, userdb, events, objects, search.
- Keys rewrite per company/project.
- Quotas: ops/min, max value bytes; overlay settings в основном для **cache**; `redis_required=False` → in-memory и слабые квоты.

Managers: Redis / Mongo / OpenSearch / Kafka / object store — optional flags в settings.

### ACL смежные

- [`cabinets/access.py`](../../../apps/api/src/prodavan/application/cabinets/access.py), [`project_service/access.py`](../../../apps/api/src/prodavan/application/project_service/access.py)
- [`runtime_guard.py`](../../../apps/api/src/prodavan/application/agent/runtime_guard.py) — running pod required
- Module session scope: `X-Prodavan-Session-Id`, `scope.chats=current|all` (PRODUCT)
- Signed ingress webhooks: [`signed_ingress.py`](../../../apps/api/src/prodavan/application/projects/signed_ingress.py)

### Метрики / биллинг

- [`budget_service.py`](../../../apps/api/src/prodavan/application/agent/budget_service.py) — enforce before turn
- Admin metrics rebuild/series; Redis counters + PG usage
- Company policy: tokens/USD/run, MCP tool policy

## Проблемы

### API-P1a — слабая подпись Bridge JWT

**Приоритет:** P1 (граничит с P0 security)  
HS256 + fallback secret chain. Компромисс API / утечка `auth_test_secret` → подделка любого pod JWT.

### API-P1b — CORS credentials + wildcards

**Приоритет:** P1  
`allow_credentials=True`, methods/headers `*`. Опасно при неверной prod origins конфигурации.

### API-P1c — широкая allowlist-поверхность

**Приоритет:** P1  
Regex включает весь `/agent/` проекта для любого распознанного pod-cred. Middleware всё ещё детектит shared token как pod-cred (хотя `get_agent_auth` его режет).

### API-P1d — archive в память

**Приоритет:** P1  
512 MB `BytesIO` на hydrate → OOM при конкуренции.

### API-P1e — literal auth env в Pod

**Приоритет:** P1  
`PRODAVAN_AUTH_TOKEN` value в pod YAML видим в describe / environ.

### API-P2a — lease secret без audit

**Приоритет:** P2  
Secret в JSON ответа lease; нет обязательного audit trail per-lease.

### API-P2b — квоты infra завязаны на Redis cache overlay

**Приоритет:** P2  
Без Redis квоты фактически выключены; другие плоскости слабо конфигурируются env.

### API-P2c — stub adapters flag

**Приоритет:** P2  
`agent_inprocess_adapters_enabled` может открыть stub path в неверном env.

### API-P2d — webhook existence leak

**Приоритет:** P2  
404 project до 503 «webhook not configured».

### API-P2e — query-time isolation gaps

**Приоритет:** P2  
Риск недостаточной tenant-фильтрации на OpenSearch/objects/Kafka consumer paths (проверять каждый adapter: index prefix / key rewrite / topic partition). Defense должен быть в каждом backend, не только в JWT scope check на входе.

## Target-design

### AuthN/Z

1. Bridge JWT: **RS256 + JWKS** (или Vault-managed HS256 без fallback). Fail closed без secret.
2. Убрать shared Bearer detection из middleware prod path полностью.
3. Allowlist: явный route table per scope (`SCOPE_INTERNAL_HYDRATE` → только archive, …), не один широкий regex.
4. Pod secrets: только secretKeyRef / projected volume; zero literal tokens in PodSpec.

### Hydrate / credentials

5. Streaming tar (chunked) или PVC; soft cap с early abort без full buffer.
6. Lease: mTLS or short-lived wrapped secret; audit row per lease; no secret in application logs.

### Tenant infra

7. Единый `TenantInfraQuota` overlay для всех плоскостей; `*_required=True` в prod.
8. Per-tenant connection / index / key prefix enforced in **each** adapter with tests for cross-tenant deny.
9. Userdb: schema-per-project или row-level RLS — выбрать и зафиксировать в PRODUCT.

### Budget / UX ресурсов

10. Cost table per model (authoritative) + estimate soft warning; hard limit на verified usage где возможно.
11. UI управления квотами/ключами/infra — единый cabinet «Resources» (не разрозненные admin-only экраны).

### Ответственность

| Поверхность | Caller | Guard |
|-------------|--------|-------|
| External agent routes | Employee JWT | ProjectAccessPolicy + rate limit |
| Internal pods | Bridge JWT | scope + project/pod match |
| Tenant infra | Bridge JWT | scope + key rewrite + quota |
| Module data | Employee **or** Bridge | SoT + session scope |
| Admin metrics | platform.admin | separate |

## Шаги рефакторинга

1. Удалить secret fallback chain; добавить CI check «prod forbids empty bridge secret» ([`bridge.py`](../../../apps/api/src/prodavan/application/pod_identity/bridge.py), settings).
2. Переписать allowlist на scope→routes map ([`middleware.py`](../../../apps/api/src/prodavan/core/middleware.py)).
3. Streaming workspace archive ([`workspace_tar_download.py`](../../../apps/api/src/prodavan/application/pod_service/workspace_tar_download.py), [`internal/pods.py`](../../../apps/api/src/prodavan/api/internal/pods.py)).
4. PodSpec: только secretRef для auth ([`pod_spec.py`](../../../apps/api/src/prodavan/infrastructure/k8s/sandbox/pod_spec.py)).
5. Audit + harden lease ([`credential_broker.py`](../../../apps/api/src/prodavan/application/agent/credential_broker.py)).
6. Prod flags: `redis_required` / opensearch / object_store; единый quota overlay.
7. CORS: явный origin list без `*` headers в prod; credentials только при need.
8. Threat-model update: [`docs/02-architecture/threat-model.md`](../../02-architecture/threat-model.md) отразить API-P1*.

## Ссылки

- Agent isolation: [`docs/02-architecture/agent-isolation.md`](../../02-architecture/agent-isolation.md)
- Multi-tenancy: [`docs/02-architecture/multi-tenancy.md`](../../02-architecture/multi-tenancy.md)
- Secrets: [`docs/05-backend/secrets.md`](../../05-backend/secrets.md)
- PRODUCT session scope / modules: [`docs/PRODUCT.md`](../../PRODUCT.md)
