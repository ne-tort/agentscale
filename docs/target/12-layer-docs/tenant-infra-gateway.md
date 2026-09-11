# Tenant Infra Gateway

**Status:** as-built — Pod Identity Bridge, **Cache / Documents / UserDB / Events / Objects** via `:8001`, company quotas, lifecycle purge.

**Product pointer:** [PRODUCT.md](../../PRODUCT.md).  
**Related as-built:** [Document Store BC (Mongo)](../../02-architecture/ADR-document-store-mongo.md) — Pods must not open `:27017`.

## Goal

Give Project Pods a **network-isolated** window into platform capabilities without broker/MinIO/DB DSNs or access to full API `:8000`.

## As-built controls

| Layer | Control |
|-------|---------|
| NP egress | DNS + 80/443 + **TCP 8001** → `prodavan` only |
| NP ingress | From `prodavan` → sandbox `:3921` only |
| Process | `prodavan.main_pod:app` on `:8001` (pod routers); Traefik stays on `:8000` |
| Auth | Bridge JWT only; shared Bearer rejected for Pod→API |
| Allowlist | `internal/pods/`, `projects/{id}/(infra\|modules\|agent)/` |
| Hydrate | `GET /internal/pods/{pod_id}/workspace-archive` (API packs MinIO → tar) |
| Credentials | `claims.pod_id == path`; scope `internal:credentials` |
| Cache | rewrite `tenant:{company}:proj:{project}:{key}`; mandatory TTL; max keys; purge on terminate (pause keeps data for resume) |
| Documents | Mongo ns `tenant_infra`, collection `p{project}_{user}`; forced company/project fields |
| User DB | Separate DB `prodavan_userdb`, schema `p_<project>`; structured API (no raw SQL) |
| Events | Gateway log + optional Kafka topic `prodavan.tenant.events`; backlog/retention caps |
| Objects | Content assets tagged `tenant_infra` + `project:{id}`; base64 put / binary get |
| Quotas | `CompanyTenantInfraQuota` — admin `PUT …/tenant-infra-quotas`; 429 `TENANT_INFRA_QUOTA` |

## Why API hydrate (not presigned MinIO)

Presigned URLs still need egress to MinIO or a public proxy. API-mediated archive removes `:9000` from NP, keeps IAM only in `prodavan` ns, and binds download to Bridge `pod_id`/`project_id`.

## Scopes

`agent:events`, `internal:credentials`, `internal:hydrate`, `infra:cache`, `infra:docs`, `infra:userdb`, `infra:events`, `infra:objects`, `module:{id}:rows`, `module:{id}:actions`, `module:{id}:meta`.

`main_pod` uses a slim lifespan (DB/Redis/Mongo/FileStore/Kafka producer only — no workers/samplers/bootstrap).

## Pod API matrix

| Plane | Paths | Isolation | Anti-spam |
|-------|-------|-----------|-----------|
| Cache | `/infra/cache/{key}` | Redis key rewrite | ops/min, max keys, max value, **required TTL** |
| Docs | `/infra/docs/{collection}` | Mongo ns `tenant_infra` | ops/min, max collections/docs/bytes |
| UserDB | `/infra/userdb/tables…` | DB `prodavan_userdb` schema/project | ops/min, max tables/rows/bytes; no raw SQL |
| Events | `/infra/events` | project event log (+ Kafka mirror) | ops/min, max payload, max backlog, retention |
| Objects | `/infra/objects` | Content BC tags | ops/min, max objects/bytes |
| Modules data | `/projects/{id}/modules/…/data/…` | Bridge + `module:{id}:rows`; SoT write ACL | bound modules only |
| Modules meta | `/projects/{id}/modules/…/meta/documents/…` | Bridge + `module:{id}:meta`; **instance SoT only** (local / unlocked global) | marks workspace outdated |
| Modules actions | `/projects/{id}/modules/…/actions/…/invoke` | Bridge + `module:{id}:actions` | same as UI actions |

## First-party MCP (`prodavan-modules`)

On materialize, workspace gets `packages/prodavan-modules/server.py` and an entry in `mcp.json` / OpenClaw `mcp.servers`. Tools wrap the Pod module routes using `PRODAVAN_API_BASE_URL` + `PRODAVAN_AUTH_TOKEN` + `PRODAVAN_PROJECT_ID`.

## Lifecycle

On pod **terminate** (delete/purge/stop): `purge_project_tenant_infra` clears cache index, docs, userdb schema, event log/offsets, and project-tagged objects. **Pause** keeps tenant infra for resume (Bridge generation still bumps).

## Where tables/documents live

| Store | Location | Pod access |
|-------|----------|------------|
| Platform Postgres (Alembic) | `prodavan` | **Never** — no DSN |
| User tables | `prodavan_userdb` / schema `p_<project>` | Structured Gateway only |
| Documents | Mongo db `prodavan`, ns `tenant_infra` | Gateway only |
| Cache | Redis keys `tenant:…` | Gateway only |
| Events | Redis/memory log + topic `prodavan.tenant.events` | Gateway only |
| Blobs | Content/FileStore | Gateway only |
