# Tenant Infra Gateway

**Status:** foundation shipped — Pod Identity Bridge, Cache API, module-scoped routes, **Pod API `:8001`**, API-mediated hydrate (no MinIO egress). Objects / tenant Kafka consume / User DB — later.

**Product pointer:** [PRODUCT.md](../../PRODUCT.md).  
**Related as-built:** [Document Store BC (Mongo)](../../02-architecture/ADR-document-store-mongo.md) — Pods must not open `:27017`.

## Goal

Give Project Pods a **network-isolated** window into platform capabilities without broker/MinIO DSNs or access to full API `:8000`.

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
| Cache | rewrite `tenant:{company}:proj:{project}:{key}` |

## Why API hydrate (not presigned MinIO)

Presigned URLs still need egress to MinIO or a public proxy. API-mediated archive removes `:9000` from NP, keeps IAM only in `prodavan` ns, and binds download to Bridge `pod_id`/`project_id`.

## Scopes

`agent:events`, `internal:credentials`, `infra:cache`, `module:{id}:rows`, `module:{id}:actions` (reserved).

## Later

Objects via Content; tenant Kafka topics; Document Store namespace `tenant_infra`; User DB gateway.
