# MIG-EQ-SOURCE-INGESTION-03: legacy source handoff

Status: blocked. Keep the legacy catalog index as the read/write authority. No
new adapter, source run, canonical snapshot, or projection is enabled by this
handoff review.

## Real user-visible import journey

The current employee journey is **Подбор техники → Управление → Базы данных**
(`mod_equipment`, table `catalogs`, view `catalogs_settings`):

1. For a local catalog, the operator uploads CSV/XLS/XLSX via
   `POST /api/v1/cabinets/{cabinet_id}/content/upload`. The response is a
   FileRef with asset/version IDs, `storage_key`, size and SHA-256. Saving a
   `catalogs` row via
   `POST|PATCH /api/v1/cabinets/{cabinet_id}/modules/mod_equipment/data/catalogs[/{row_id}]`
   opens the file to probe headers, then queues the existing OpenSearch reindex
   when its `column_map` is ready.
2. For remote PostgreSQL, the operator sets `source_kind=remote`, a secret
   reference and table. Saving the row causes the cabinet/owner row-action hooks
   to invoke the schema/count-only `probe_catalog_remote`; the explicit owner
   action endpoint is
   `POST /api/v1/cabinets/{cabinet_id}/modules/mod_equipment/actions/probe_catalog_remote/invoke`.
   Probe results update the same row and are not a data snapshot.
3. Create/update triggers `index_catalog_opensearch`; the detail view's Reindex
   action invokes
   `POST /api/v1/cabinets/{cabinet_id}/modules/mod_equipment/actions/index_catalog_opensearch/invoke`
   with `row_id`. The action marks the row queued and calls
   `enqueue_index_equipment_catalog`. The equivalent module-owner content-upload
   and row-data paths are `POST /api/v1/admin/modules/{module_id}/content/upload`,
   `POST /api/v1/companies/{company_id}/modules/{module_id}/content/upload`,
   and the corresponding `GET|POST|PATCH /.../modules/mod_equipment/data/catalogs[/{row_id}]` routes.
   Owner action paths are
   `/api/v1/admin/modules/mod_equipment/actions/{action_id}/invoke` and
   `/api/v1/companies/{company_id}/modules/mod_equipment/actions/{action_id}/invoke`.
4. With Celery enabled, `prodavan.jobs.index_equipment_catalog` is routed to
   queue `indexing`, task ID
   `prodavan.jobs.index_equipment_catalog:{row_id}`. It opens the local FileRef
   from object storage or streams remote PostgreSQL, maps columns, then wipes
   and bulk-writes the existing per-row `equipment` index (`c_{safe_row_id}`;
   physical name `equipment__c_{safe_row_id}`). Row JSON
   stores progress and final ready/error status. If Celery is disabled, the
   helper requests inline indexing instead.
5. The Pod calls
   `GET /api/v1/projects/{project_id}/modules/mod_equipment/equipment/catalog-sources`
   and `POST /api/v1/projects/{project_id}/modules/mod_equipment/equipment/catalog-search`.
   These expose ready catalogs from
   the existing OpenSearch indexes to the equipment MCP/agent. This remains the
   agent-visible source journey.

`GET|POST /api/v1/admin/catalogs/{catalog_id}/entries` and
`PATCH|DELETE /api/v1/admin/catalogs/{catalog_id}/entries/{entry_id}` are
separate generic reference-catalog CRUD routes, not the equipment source
registry or import API. The code
equivalent to the requested `application/modules/equipment_catalogs.py` is
`application/modules/equipment_catalog_opensearch.py`; no
`equipment_catalogs.py` module exists in this worktree.

The durable source configuration is a `module_instance_data_rows` row
(`catalogs`, JSONB) plus mutable FileRef or secret reference. Local upload bytes
are in the content/object store, but indexing retrieves them by `storage_key`
and does not retain an immutable ingestion artifact. Remote indexing has no raw
artifact. Neither path writes migration `ingestion_runs` or immutable page
snapshots. Seller strings from catalog rows are mapped to `trusted_sellers` by
name; this is not a verified canonical supplier mapping. Existing OpenSearch
documents expose a supplier label and `src_hash`, not canonical source/supplier,
run, artifact, snapshot or row-provenance identity.

## Blocker

No `equipment_sources` CRUD/source API or source-registry persistence model
exists in this worktree. The term occurs in migration target documents, not as
an implemented route. Some adjacent migration scaffolding does exist:
`application/equipment_ingestion` defines generic S4B web/price-file and IMAP
contracts plus an orchestration service, and `equipment_v2_*` ORM models define
basic run/snapshot/offer tables. They do not provide a concrete `mod_equipment`
local-file/remote-PostgreSQL adapter, source repository, run repository,
checkpoint/CAS evidence store, raw-object metadata bridge or API/route/worker
wiring. The generic `SourceConfig` carries a tenant but no owner/project or
verified supplier mapping; its run repository boundary is whole-batch
start/claim/complete/fail rather than atomic page evidence/checkpoint commits.
`equipment_v2` is explicitly unregistered from legacy module code.

The sibling `application/migration/equipment_api_adapter` is a read-only,
port-driven projection/compatibility adapter. It has no concrete production
read-port implementation, route composition or source-import dispatch. Its
catalog query consumes already accepted typed `CatalogSnapshot`s; it does not
acquire from `mod_equipment.catalogs` or write a search index. The existing
`equipment_v2` models do not include a durable raw artifact row. Consequently
there is no durable source/store bridge. `mod_equipment.catalogs` remains the
only sanctioned source-import configuration path, but its owner context and
local file/remote connection have no evidenced mapping to canonical tenant,
owner, project, source and **verified supplier IDs**. Do not infer identity
from seller/display name, brand, URL, row ID or payload.

The current source-open-before-delete guard preserves the physical old index on
source-open failure, but `run_index_equipment_catalog` then marks the catalog row
`error`. The reader (`_ready_catalogs`) admits only `status=ready`, so the
catalog and old index disappear from user/agent search despite the old physical
documents remaining. After index deletion, a bulk/index failure may also leave
an incomplete index, and `error` likewise excludes it from search. This does not
implement user-visible last-good fallback. Existing Celery is durable execution
infrastructure, but the task is a mutable full-index rebuild, not a
run/checkpoint/page-snapshot commit. No sanctioned source can feed the immutable
contract end-to-end without fabricating identity/evidence or double-indexing.
Both are rejected.

## Required handoff contract

- Keep the current user journey/routes. Row create/update and explicit Reindex
  must converge on a single handoff; do not add a second UI/API import path.
  Apply the same gate to platform/company owner-module route variants.
- Resolve module-instance canonical tenant/owner/project scope and reviewed
  source/supplier mapping before dispatch. Pin stable source ID/kind/schema,
  source revision semantics, local FileRef version or remote secret reference
  and table, column mapping, and explicit verified source-supplier-key to
  canonical supplier-ID mapping. Missing/ambiguous identity blocks without
  positive writes.
- In one Postgres transaction persist the ingestion run and enqueue-outbox row
  with the existing source-row/action state. Reuse the registered
  `prodavan.jobs.index_equipment_catalog` task and `indexing` queue; evolve that
  worker's dispatch path, not a second worker or dual indexer. Dispatch payload
  carries stable `source_id`, `run_id`, expected source revision/checkpoint, and
  no credentials or raw bytes. Idempotency is per run/page, not current row-only
  task ID.
- Read only the bound local file version or remote configuration. For each
  bounded page, write exact raw bytes to an immutable, digest-addressed object
  key and verify length/SHA-256. One Postgres transaction inserts artifact
  metadata, exact page snapshot/offers/quarantine rows, run counters and
  checkpoint using compare-and-swap. Exact replay is idempotent; changed content
  at the same run/page key conflicts. A blob left by a failed DB transaction is
  unreferenced, not accepted evidence, and is eligible only for retention-aware
  garbage collection.
- On source/store outage record a failure marker without changing accepted
  evidence. Retry at the last committed checkpoint; expose last-good as stale
  without mutating its stored snapshot. Failed/partial runs never become
  last-good.
- Build search documents only from accepted snapshots and explicit field
  mappings. Persist immutable projection generations; atomically advance a
  last-good pointer using CAS on expected generation and complete scoped input
  digest. Keep the current index as read owner until publication and identity
  mappings pass acceptance. Never dual-write both search paths for one import.
- Route response distinguishes `blocked`, `queued`, and existing `ready/error`.
  Return run/task IDs only after durable run/outbox commit. Poll durable run state,
  not Celery result state, for acceptance.

Required durable records: reviewed `equipment_sources` source/supplier binding;
`ingestion_runs` with checkpoint/counters; immutable artifact metadata and
per-page catalog snapshots/offers/quarantine; source failure markers; enqueue
outbox; versioned projection-generation metadata and last-good pointer. Schema,
API/store implementation and canonical mapping approval are prerequisites, not
part of this narrow migration-owned slice. Until supplied and accepted, keep
the current catalogs route, Celery indexer and OpenSearch index authoritative.
