# Tenant Infra Gateway (design)

**Status:** design only — not implemented. First consumer intent: S4B MCP cache later.

**Product pointer:** [PRODUCT.md](../../PRODUCT.md).  
**Related as-built:** [Document Store BC (Mongo)](../../02-architecture/ADR-document-store-mongo.md) — app-side documents; Pods still must not open `:27017`.

## 1. Goal and non-goals

### Goal

Give Project Pods (agent + MCP packages) a **small, authenticated window** into platform infrastructure (cache, objects, events, optional user DB / documents) without exposing broker credentials or widening NetworkPolicy to Redis / Kafka / Postgres / Mongo.

### Non-goals

- Handing Redis/Kafka/Postgres/Mongo DSNs (or IAM keys) to the Pod.
- Using Kafka as the Pod↔infra **data-plane** for latency-sensitive cache (Kafka stays an internal BC bus).
- Turning Tenant Infra into a separate deployable microservice in v1 (same process as Metrics / Document Store).
- Replacing Content Service ACL — object access reuses Content with bridge principal.

## 2. Problem (as-built)

| Constraint | Reality |
|------------|---------|
| Sandbox NP egress | API `:8000` + MinIO `:9000` + DNS + internet 80/443 |
| Blocked | Redis 6379, Kafka 9092, Postgres 5432, **Mongo 27017**, Keycloak, … |
| Pod→API auth | Shared cluster Bearer (`agent_auth`) — **gap** for tenant data plane |
| Content ACL | Lives in API; good pattern to copy |

Direct Redis/Kafka/PG/Mongo + “unique prefixes” is **insufficient**: prefix is convention, not enforcement. Leaked DSN = whole shared broker.

```text
Project Pod (agent / S4B MCP)
  → scoped pod JWT
  → Pod Identity Bridge
  → TenantInfraGateway BC  and/or  Content Service
  → Redis / Kafka / PG / MinIO / DocumentStore(Mongo)  (API process only)
```

## 3. Prerequisite: Pod Identity Bridge

Replace shared `pod_agent_bridge_auth_token` with a **scoped** credential minted at launch/resume:

| Claim | Required | Notes |
|-------|----------|--------|
| `project_id` | yes | Hard bind to one project |
| `cabinet_id` | yes | |
| `company_id` | yes | Tenancy root |
| `acting_employee_id` / `session_id` | optional | Audit / chat correlation |
| `exp` / `jti` | yes | TTL; revoke on pause/stop/delete |

Rules:

- All infra (and preferably content) calls from the Pod use **only** this token.
- Revoke list or generation bump on pause/stop so stolen tokens die with the pod lifecycle.
- Shared bridge token remains a **known gap** until Bridge ships — do not expand tenant data plane on it.

## 4. BC layout (in-proc “microservice”)

Package: `application/tenant_infra/` (same process as Metrics / Document Store — not a separate k8s Deployment).

| Surface | Path |
|---------|------|
| HTTP | `/api/v1/projects/{project_id}/infra/...` |
| Auth | Pod Identity Bridge JWT; path `project_id` must match claim |
| Sync calls out | Ports only: Redis, Content, Kafka publish helper, DocumentStoreService, optional PG gateway |
| Cross-BC imports | No “reach into” other BC internals; adapters behind ports |
| Brokers | Reachable **only** from `prodavan-api` / celery in ns `prodavan` |

Isolation is logical (imports + auth + rewrite), not process — same pattern as Metrics BC.

## 5. Capabilities (implementation order)

### 5.1 Cache (Redis) — first

- Ops: `GET` / `SET` / `DEL` / `INCR` (TTL optional).
- Server **rewrites** keys to  
  `tenant:{company_id}:proj:{project_id}:{user_key}`.
- Deny keys that escape rewrite or collide with platform prefixes (`prodavan:`, `celery*`, metrics keys, …).
- Quotas (company settings): max keys, max value bytes, ops/min.

### 5.2 Objects (MinIO via Content)

- Reuse Content Service with bridge principal (company/employee ACL + storage accounting).
- Do **not** hand MinIO IAM to the Pod (hydrate secret stays initContainer-only).

### 5.3 Events (Kafka) — tenant topics only

- Publish/consume only under prefix `tenant.{company_id}.…` (exact allow-list).
- Rate and payload size limits; deny platform topics (`prodavan.platform.*`, `prodavan.metrics.*`, `prodavan.document.*`, auth, relation, …).
- Not a substitute for Document Store or Metrics buses used by in-proc BCs.

### 5.4 User DB (Postgres)

- Provision schema/role per company or project; **no raw DSN in Pod**.
- SQL via gateway prepared statements or short-lived scoped proxy; connection and size limits.
- Never open sandbox egress to `:5432`.

### 5.5 Documents (Mongo via Document Store)

- Call in-proc `DocumentStoreService` with namespace `tenant_infra` (or `tenant_infra` + collection per project).
- Always pass `company_id` / `project_id` from bridge claims; service tenancy checks apply.
- Physical Mongo collections remain `{namespace}.{collection}` inside API — Pod never sees Mongo URL.
- Deny arbitrary namespaces from Pod (only gateway-owned namespace).

## 6. ACL and isolation rules

| Layer | Rule |
|-------|------|
| Network | Sandbox NP: **no** 6379 / 9092 / 5432 / 27017 |
| Auth | Scoped JWT; path project must match claim |
| Rewrite | Server-side key/topic/collection rewrite; deny-list platform spaces |
| Quotas | Company (and optional project) caps next to agent / cabinet quotas admin |
| System vs tenant | Keyspaces must not intersect |
| Enforcement | Never “MCP promised a prefix” |

## 7. Kafka for the gateway itself

Gateway publishes **its own** side-effect events (for Metrics and audit), not Pod-chosen platform buses:

| Event family | Purpose |
|--------------|---------|
| `tenant_infra.op` | Successful cache/object/event/doc op (sampled or counted) |
| `tenant_infra.denied` | ACL / rewrite / allow-list reject |
| `tenant_infra.quota_hit` | Soft/hard quota |

Plus `metrics.counter.delta` (and later storage snapshots) so Metrics BC can account usage.

Do **not** mix these onto `prodavan.platform.events` unless a platform lifecycle event is truly required.

## 8. Threats and controls

| Threat | Control |
|--------|---------|
| DSN / IAM leak into Pod env | Never inject broker secrets; hydrate uses separate sandboxes secret |
| Key/topic path traversal | Canonicalize + rewrite; reject `..`, absolute paths, null bytes |
| Cross-tenant read/write | Claims + rewrite include `company_id`/`project_id`; tests for swap attacks |
| DoS / noisy neighbor | Quotas ops/min, value size, key count; rate limit HTTP |
| Auth confusion (shared bridge token) | Block tenant infra until Pod Identity Bridge; shared token insufficient |
| Confused deputy via Content | Bridge principal scoped; Content ACL still enforced |
| Mongo namespace escape | Gateway hard-codes namespace; Document Store validates slugs |

## 9. Rollout phases

1. **Pod Identity Bridge** (prerequisite).
2. **Cache API** (Redis) — S4B MCP first consumer.
3. **Objects** via Content + bridge principal.
4. **Tenant events** (Kafka prefix).
5. **Documents** (Document Store) and/or **User DB** (Postgres gateway).

S4B today: URL/login/password + MCP zip; **no Redis from Pod**. Later: S4B MCP → Cache API with scoped identity.

## 10. Relation to Document Store BC

| Client | Path |
|--------|------|
| In-proc app BC | `DocumentStoreService` / Port directly (e.g. `equipment`, `content`) |
| Project Pod | **Only** Tenant Infra Gateway → Document Store with `tenant_infra` namespace |

Document Store Kafka `document.*` events remain internal; gateway may emit additional `tenant_infra.*` for Pod-originated ops.

## 11. Why not a microservice yet

Same as Metrics / Document Store: package boundary + HTTP surface is enough until scale or blast-radius demands a split. Logical isolation (imports + auth + NP) comes first.
