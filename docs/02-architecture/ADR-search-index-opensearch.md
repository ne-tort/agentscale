# ADR: Search Index BC on OpenSearch

**Status:** accepted (2026-09)

## Context

The platform needs a professional in-process **search / indexing adapter** for application BCs (full-text and structured document indexes), separate from Postgres transactional schema and from Document Store (Mongo).

Sandbox pods must not receive OpenSearch URLs. Project container access to search remains a future Tenant Infra Gateway plane (same pattern as Document Store docs).

This does **not** replace equipment catalog search (`equipment_catalog_search` / SQLite + remote SQL) in the first iteration.

## Decision

1. Deploy OpenSearch in GitOps (`prodavan-opensearch` StatefulSet in `infra/k3s/base/platform/`).
2. Add in-proc BC `application/search_index/` with `SearchIndexPort`, OpenSearch HTTP adapter (httpx), and in-memory adapter for tests.
3. Physical indexes are `{namespace}__{index}`; namespace = calling BC id; server enforces slug validation and tenancy (`company_id` required unless namespace ∈ platform/system).
4. Domain Kafka bus `search` → topic `prodavan.search.events`; metrics via existing `metrics.counter.delta`.
5. Admin HTTP under `/api/v1/admin/search-index/*` (`platform.admin` only).
6. MVP: OpenSearch **security plugin disabled** locally; ACL + quotas live in `SearchIndexService`; sandbox NetworkPolicy keeps **no** egress to `:9200`.
7. Company ownership for empty indexes is stored in OpenSearch `mappings._meta.company_id` (never in index `settings` — unknown settings 400).
8. Project Pods do **not** open `:9200` — search goes through Tenant Infra `/infra/search` + equipment `catalog-search` (Bridge scope `infra:search`).

## Consequences

- Other BCs call `SearchIndexService` / Port synchronously; Kafka is side-effects only.
- Ops: `OPENSEARCH_URL` in API ConfigMap; init Job smokes `/_cluster/health`; PVC `prodavan-opensearch-data`.
- MVP omits security plugin, ISM policies, multi-node, and product catalog migration onto OpenSearch.
