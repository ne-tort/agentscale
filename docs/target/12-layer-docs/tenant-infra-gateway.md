# Tenant Infra Gateway (design)

**Status:** design only — not implemented. First consumer intent: S4B MCP cache later.

**Product pointer:** [PRODUCT.md](../../PRODUCT.md).

## Problem

Project Pods (agent + MCP packages) need platform infrastructure (Redis cache, object storage, events, optional user DB) without:

- opening NetworkPolicy egress to Redis / Kafka / Postgres;
- sharing platform broker credentials with the sandbox;
- relying on client-side key/topic “prefixes” as security.

As-built today: sandbox NP allows API + MinIO + internet; Redis/Kafka/PG are blocked. Content ACL lives in the API. Pod→API auth is still a **shared** cluster Bearer (`agent_auth.py`) without employee/company principal — a gap for any tenant infra surface.

## Verdict

| Approach | Assessment |
|----------|------------|
| Direct Redis/Kafka/PG + unique prefixes | **Insufficient.** Prefix is convention, not enforcement. Leaked DSN = whole shared broker. |
| Shared cluster pod token | **Insufficient** for tenant data plane. |
| **HTTP API gateway (Content-like)** | **Target.** Pod → HTTPS → in-process BC; ACL / quotas / key rewrite on the platform; brokers only reachable from `prodavan-api`. |

Do **not** use Kafka as the Pod↔infra data-plane transport for cache. Kafka stays an internal BC bus. Latency-sensitive cache = synchronous HTTP.

```text
Project Pod (agent / S4B MCP)
  → scoped pod JWT
  → Pod Identity Bridge
  → TenantInfraGateway BC  and/or  Content Service
  → Redis / Kafka / PG / MinIO  (API process only)
```

## Components

### 1. Pod Identity Bridge (prerequisite)

Replace shared `pod_agent_bridge_auth_token` with a **scoped** credential minted at launch:

- Claims: `project_id`, `cabinet_id`, `company_id`, optional `acting_employee_id` / session id
- TTL + revoke on pause/stop
- All infra and content calls from the Pod use this token only

### 2. TenantInfraGateway BC

In-process package `application/tenant_infra/` (same process as Metrics / Content — not a separate microservice).

- No cross-BC imports outward; HTTP only: `/api/v1/projects/{id}/infra/...`
- Server **rewrites** keys/topics into a tenant namespace; deny writes outside it
- Deny-list platform namespaces (`prodavan:*`, platform Kafka topics)

### 3. Capabilities (implementation order after this design)

1. **Cache (Redis)** — `GET/SET/DEL/INCR` under  
   `tenant:{company_id}:proj:{project_id}:…`  
   Quotas in company settings: max keys, max value bytes, ops/min.
2. **Objects** — reuse Content Service with bridge principal (company/employee ACL + storage accounting). Do not hand MinIO IAM to the Pod.
3. **Events (Kafka)** — publish/consume only tenant topics / prefix `tenant.{company_id}.…`; rate/size limits.
4. **User DB** — provision schema/role per company or project; **no raw DSN in Pod** — SQL via gateway or short-lived scoped proxy; size/conn limits.

## Isolation rules

- Enforcement is **server-side** (rewrite + deny), never “MCP promised a prefix”.
- System and tenant keyspaces must not intersect.
- Company quotas sit next to existing agent policy / cabinet quotas admin surfaces.

## Why not a microservice yet

Same pattern as Metrics BC: package boundary + API surface is enough until scale demands a split. Isolation is logical (imports + auth), not process.

## S4B today vs later

- **Now:** S4B page stores URL/login/password (secret) + MCP zip; materialize unpacks MCP; Pod gets `S4B_*` env. MCP talks to the S4B site; **no Redis from Pod**.
- **Later:** S4B MCP calls Cache API with scoped pod identity instead of needing Redis in the sandbox.
