# Tenant Infra Gateway

**Status:** foundation in progress (Pod Identity Bridge + Cache API + module-scoped pod routes). Objects / tenant Kafka / User DB / dedicated `:8001` — later phases.

**Product pointer:** [PRODUCT.md](../../PRODUCT.md).  
**Related as-built:** [Document Store BC (Mongo)](../../02-architecture/ADR-document-store-mongo.md) — app-side documents; Pods still must not open `:27017`.

## 1. Goal and non-goals

### Goal

Give Project Pods (agent + MCP packages) a **small, authenticated window** into platform infrastructure (cache, objects, events, optional user DB / documents) and **bound module data APIs** without exposing broker credentials or widening NetworkPolicy to Redis / Kafka / Postgres / Mongo.

### Non-goals

- Handing Redis/Kafka/Postgres/Mongo DSNs (or IAM keys) to the Pod.
- Using Kafka as the Pod↔infra **data-plane** for latency-sensitive cache (Kafka stays an internal BC bus).
- Turning Tenant Infra into a separate deployable microservice in v1 (same process as Metrics / Document Store).
- Replacing Content Service ACL — object access reuses Content with bridge principal.
- Per-module Deployments or path filtering in NetworkPolicy (NP is port/host only).

## 2. Problem (as-built)

| Constraint | Reality |
|------------|---------|
| Sandbox NP egress | API `:8000` + MinIO `:9000` + DNS + internet 80/443 |
| Blocked | Redis 6379, Kafka 9092, Postgres 5432, **Mongo 27017**, Keycloak, … |
| Pod→API auth (legacy) | Shared cluster Bearer — insufficient for tenant data plane |
| Pod→API auth (target) | **Pod Identity Bridge JWT** with project/module scopes |
| Content ACL | Lives in API; good pattern to copy |

Direct Redis/Kafka/PG/Mongo + “unique prefixes” is **insufficient**: prefix is convention, not enforcement. Leaked DSN = whole shared broker.

```text
Project Pod (agent / S4B MCP)
  → scoped pod JWT (scopes)
  → Pod Identity Bridge + allowlist middleware
  → Pod API surface only
       ├─ /projects/{id}/infra/...     → TenantInfraGateway adapters
       ├─ /projects/{id}/modules/...   → bound module rows/actions
       └─ /agent/... , /internal/pods/ → existing agent bridge
  → Redis / Kafka / PG / MinIO / DocumentStore(Mongo)  (API process only)
```

## 3. Pod API surface (selective isolation)

NetworkPolicy **cannot** isolate HTTP paths — only TCP to `prodavan-api:8000`. Selective isolation is **auth + allowlist**:

| Layer | Mechanism |
|-------|-----------|
| Network | Sandbox may reach API `:8000` and MinIO `:9000` only (no brokers) |
| Middleware | Bearer that is shared pod token **or** Bridge JWT → **403** unless path matches Pod surface allowlist |
| Bridge JWT | Claims: `project_id`, `cabinet_id`, `company_id`, `pod_id`, `gen`, `scopes[]`, `exp`/`jti` |
| Scopes | Built at launch from bound modules + platform pod scopes |
| Employee JWT | Unchanged; Bridge JWT is **not** an OIDC employee token |

### Allowlist (deny-by-default for pod credentials)

- `/api/v1/agent/...`
- `/api/v1/internal/pods/...`
- `/api/v1/projects/{project_id}/infra/...`
- `/api/v1/projects/{project_id}/modules/...`

Admin, company, cabinet (employee), OIDC — **not** on the allowlist. Path `project_id` / `module_id` must match Bridge claims/scopes.

### Scopes (examples)

| Scope | Meaning |
|-------|---------|
| `agent:events` | Agent session events |
| `internal:credentials` | Credential broker |
| `infra:cache` | Tenant Infra Cache API |
| `module:{module_id}:rows` | Rows CRUD for that bound module |
| `module:{module_id}:actions` | Module actions (later) |

Declarative `mcp_tools` / platform `cabinet.*` from the Pod must call **these** scoped routes (or agent tools that do), never admin/OIDC surfaces.

### Defense-in-depth (later)

Optional K8s Service on `:8001` mounting only Pod routers so NP can target a narrower port. v1 uses allowlist on `:8000`.

## 4. Prerequisite: Pod Identity Bridge

Replace reliance on shared `pod_agent_bridge_auth_token` for tenant data with a **scoped** JWT minted at launch/resume:

| Claim | Required | Notes |
|-------|----------|--------|
| `project_id` | yes | Hard bind to one project |
| `cabinet_id` | yes | |
| `company_id` | yes | Tenancy root |
| `pod_id` | yes | Lifecycle bind |
| `gen` | yes | Generation; bump on pause/stop/delete |
| `scopes` | yes | List of scope strings |
| `acting_employee_id` / `session_id` | optional | Audit / chat correlation |
| `exp` / `jti` | yes | TTL; jti for audit |

Rules:

- Infra and module pod routes accept **only** Bridge JWT (not shared cluster Bearer).
- Shared Bearer may remain for legacy agent/events until fully migrated; middleware still confines it to the allowlist.
- Revoke via **generation bump** (Redis) on pause/stop/delete/reload so stolen tokens die with pod lifecycle.
- Inject minted JWT into Pod as `PRODAVAN_AUTH_TOKEN` env value (per-pod); keep `BRIDGE_AUTH_TOKEN` (API→runtime) on the cluster Secret.

## 5. BC layout (in-proc “microservice”)

Package: `application/tenant_infra/` (same process as Metrics / Document Store — not a separate k8s Deployment).

| Surface | Path |
|---------|------|
| HTTP | `/api/v1/projects/{project_id}/infra/...` |
| Auth | Pod Identity Bridge JWT; path `project_id` must match claim; scope `infra:cache` (etc.) |
| Sync calls out | Ports only: Redis, Content, Kafka publish helper, DocumentStoreService, optional PG gateway |
| Cross-BC imports | No “reach into” other BC internals; adapters behind ports |
| Brokers | Reachable **only** from `prodavan-api` / celery in ns `prodavan` |

Isolation is logical (imports + auth + rewrite), not process — same pattern as Metrics BC.

## 6. Capabilities (implementation order)

### 6.1 Cache (Redis) — first

- Ops: `GET` / `SET` / `DEL` / `INCR` (TTL optional).
- Server **rewrites** keys to  
  `tenant:{company_id}:proj:{project_id}:{user_key}`.
- Deny keys that escape rewrite or collide with platform prefixes (`prodavan:`, `celery*`, metrics keys, …).
- Quotas: max value bytes, ops/min (rate limit).

### 6.2 Objects (MinIO via Content) — later

- Reuse Content Service with bridge principal (company/employee ACL + storage accounting).
- Do **not** hand MinIO IAM to the Pod (hydrate secret stays initContainer-only).

### 6.3 Events (Kafka) — tenant topics only — later

- Publish/consume only under prefix `tenant.{company_id}.…` (exact allow-list).
- Rate and payload size limits; deny platform topics (`prodavan.platform.*`, `prodavan.metrics.*`, `prodavan.document.*`, auth, relation, …).
- Not a substitute for Document Store or Metrics buses used by in-proc BCs.

### 6.4 User DB (Postgres) — later

- Provision schema/role per company or project; **no raw DSN in Pod**.
- SQL via gateway prepared statements or short-lived scoped proxy; connection and size limits.
- Never open sandbox egress to `:5432`.

### 6.5 Documents (Mongo via Document Store) — later

- Call in-proc `DocumentStoreService` with namespace `tenant_infra` (or `tenant_infra` + collection per project).
- Always pass `company_id` / `project_id` from bridge claims; service tenancy checks apply.
- Physical Mongo collections remain `{namespace}.{collection}` inside API — Pod never sees Mongo URL.
- Deny arbitrary namespaces from Pod (only gateway-owned namespace).

## 7. ACL and isolation rules

| Layer | Rule |
|-------|------|
| Network | Sandbox NP: **no** 6379 / 9092 / 5432 / 27017; allow API `:8000` + MinIO `:9000` + DNS + 80/443 |
| Auth | Scoped JWT; path project/module must match claim/scopes |
| Allowlist | Pod credentials cannot call admin/OIDC/other project APIs |
| Rewrite | Server-side key/topic/collection rewrite; deny-list platform spaces |
| Quotas | Ops/min and value size on Cache API |
| System vs tenant | Keyspaces must not intersect |
| Enforcement | Never “MCP promised a prefix” |

## 8. Kafka for the gateway itself

Gateway publishes **its own** side-effect events (for Metrics and audit), not Pod-chosen platform buses:

| Event family | Purpose |
|--------------|---------|
| `tenant_infra.op` | Successful cache/object/event/doc op (sampled or counted) |
| `tenant_infra.denied` | ACL / rewrite / allow-list reject |
| `tenant_infra.quota_hit` | Soft/hard quota |

Plus `metrics.counter.delta` (and later storage snapshots) so Metrics BC can account usage.

Foundation may emit audit via platform event bus with these `event_type` values until a dedicated topic exists.

Do **not** mix Pod-chosen traffic onto `prodavan.platform.events` unless a platform lifecycle event is truly required.

## 9. Threats and controls

| Threat | Control |
|--------|---------|
| DSN / IAM leak into Pod env | Never inject broker secrets; hydrate uses separate sandboxes secret |
| Key/topic path traversal | Canonicalize + rewrite; reject `..`, absolute paths, null bytes |
| Cross-tenant read/write | Claims + rewrite include `company_id`/`project_id`; tests for swap attacks |
| Whole-API access with pod token | Allowlist middleware + scopes |
| DoS / noisy neighbor | Quotas ops/min, value size; rate limit HTTP |
| Auth confusion (shared bridge token) | `/infra` and `/modules` require Bridge JWT; shared token insufficient |
| Confused deputy via Content | Bridge principal scoped; Content ACL still enforced |
| Mongo namespace escape | Gateway hard-codes namespace; Document Store validates slugs |

## 10. Rollout phases

1. **Pod Identity Bridge + allowlist** (prerequisite).
2. **Cache API** (Redis) — S4B MCP first consumer.
3. **Module-scoped pod routes** (`/projects/{id}/modules/{mid}/...` + scopes from bound modules).
4. **Objects** via Content + bridge principal. *(separate PR)*
5. **Tenant events** (Kafka prefix). *(separate PR)*
6. **Documents** (Document Store) and/or **User DB** (Postgres gateway); optional Service `:8001`. *(separate PR)*

S4B today: URL/login/password + MCP zip; **no Redis from Pod**. Later: S4B MCP → Cache API with scoped identity.

## 11. Relation to Document Store BC

| Client | Path |
|--------|------|
| In-proc app BC | `DocumentStoreService` / Port directly (e.g. `equipment`, `content`) |
| Project Pod | **Only** Tenant Infra Gateway → Document Store with `tenant_infra` namespace |

Document Store Kafka `document.*` events remain internal; gateway may emit additional `tenant_infra.*` for Pod-originated ops.

## 12. Why not a microservice yet

Same as Metrics / Document Store: package boundary + HTTP surface is enough until scale or blast-radius demands a split. Logical isolation (imports + auth + NP) comes first.
