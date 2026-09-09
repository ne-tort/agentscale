# ADR: Document Store BC on MongoDB

**Status:** accepted (2026-09)

## Context

The platform needs a professional in-process **non-relational document service** for application BCs (flexible documents, indexes), separate from Postgres transactional schema.

Sandbox pods must not receive Mongo DSNs. Project container access to infra remains a future Tenant Infra Gateway ([tenant-infra-gateway.md](../target/12-layer-docs/tenant-infra-gateway.md)).

This is **not** the deferred cutover of product `module_instances` JSONB to Mongo ([ADR-backlog-module-instance-mongo.md](ADR-backlog-module-instance-mongo.md)) — module instance data stays on Postgres.

## Decision

1. Deploy MongoDB in GitOps (`prodavan-mongodb` StatefulSet in `infra/k3s/base/platform/`).
2. Add in-proc BC `application/document_store/` with `DocumentStorePort`, Mongo adapter (motor), and in-memory adapter for tests.
3. Physical collections are `{namespace}.{collection}`; namespace = calling BC id; server enforces slug validation and tenancy (`company_id` required unless namespace ∈ platform/system).
4. Domain Kafka bus `document` → topic `prodavan.document.events`; metrics via existing `metrics.counter.delta`.
5. Admin HTTP under `/api/v1/admin/document-store/*` (`platform.admin` only).
6. Project Pods access documents only via Tenant Infra Gateway (`/projects/{id}/infra/docs/*`, namespace forced `tenant_infra`) — never `:27017` and never admin routes.
7. Sandbox NetworkPolicy keeps **no** egress to `:27017`.

## Consequences

- Other BCs call `DocumentStoreService` / Port synchronously; Kafka is side-effects only.
- Ops: `MONGODB_URL` in API secrets; init Job creates app user; PVC `prodavan-mongodb-data`.
- MVP omits aggregations, multi-doc transactions, and change streams.
