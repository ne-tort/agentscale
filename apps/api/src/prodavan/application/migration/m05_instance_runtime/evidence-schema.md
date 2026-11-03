# M05 evidence relations required by the durable adapter

Status: required schema contract, authored under the migration verification freeze.
Owner task: `MIG-M05-BACKFILL-03`.

`instance_persistence.py` implements the durable M05 ports against relations that exist in the
migration chain (`m05_plugin_instances`, `m05_plugin_instance_registry_links`,
`m04e_dynamic_app_definitions`, `m04e_dynamic_app_versions`, `scope_registry`) **and** against
five relations that revision `2026110303` does not create. This file freezes their exact shape
so the follow-up DDL revision is mechanical, and so a reviewer can check the adapter's SQL
column-by-column.

Nothing here is DDL in the repository: no Alembic revision, ORM model or executed statement is
added by this task. `verify_evidence_relations()` is a read-only probe
(`to_regclass` + `information_schema.columns`) and raises `m05_evidence_schema_incomplete` when
any relation or column is absent, so a missing relation blocks the partition instead of silently
degrading into an in-memory cursor.

## 1. `m05_plugin_instance_source_snapshot` — immutable pinned source rows

The only relation the candidate reader may read. A page is addressed by
`(tenant_id, app_definition_id, source_snapshot_id, source_schema_version, source_snapshot_digest)`;
`source_id` is the durable ordering key.

| Column | Type | Notes |
|---|---|---|
| `tenant_id` | `String(40)` not null | partition, canonical `companies.id` |
| `app_definition_id` | `String(80)` not null | partition |
| `source_snapshot_id` | `String(200)` not null | pinned snapshot identity |
| `source_schema_version` | `String(40)` not null | pinned reader/schema version |
| `source_snapshot_digest` | `String(71)` not null | `sha256:<64 hex>` |
| `page_position` | `Integer` not null | 1-based order within the snapshot |
| `source_kind` | `String(80)` not null | legacy source table discriminator |
| `source_id` | `String(80)` not null | exact legacy key, verbatim |
| `payload` | `JSONB` not null | immutable source payload used only for the digest |
| `payload_digest` | `String(71)` not null | `sha256:` of the canonical payload |
| `plugin_instance_id`, `version_id`, `app_id`, `manifest_version`, `scope_id`,
  `artifact_digest`, `subject_id`, `session_id` | text not null | resolved identity of the row |
| `cabinet_id`, `project_id` | `String(40)` null | installation anchors |
| `lifecycle` | `String(16)` not null | source lifecycle, may be non-publishable |
| `availability` | `String(16)` not null | `active`, `stopped`, `archived`, `quarantined` |
| `runtime_generation`, `writer_generation` | `Integer` not null | non-negative |

Constraints: primary key
`(tenant_id, app_definition_id, source_snapshot_id, source_schema_version, source_kind, source_id)`;
unique `(tenant_id, app_definition_id, source_snapshot_id, source_snapshot_digest, page_position)`;
`payload_digest ~ '^sha256:[0-9a-f]{64}$'`; both generations `>= 0`.

`page_position` is deliberately **not** named `position`: `POSITION` is a reserved PostgreSQL
keyword that cannot be used as an unquoted column name.

## 2. `m05_plugin_instance_import_checkpoints` — the durable page cursor

One row per `(tenant, definition, snapshot, schema)`. The cursor never advances across a
snapshot lineage.

| Column | Type | Notes |
|---|---|---|
| `tenant_id` | `String(40)` not null | partition |
| `definition_id` | `String(80)` not null | partition |
| `source_snapshot_id` | `String(200)` not null | |
| `source_schema_version` | `String(40)` not null | |
| `source_snapshot_digest` | `String(71)` not null | changed digest = lineage change |
| `after_source_id` | `String(80)` null | last committed `source_id`; `NULL` iff `sequence = 0` |
| `sequence` | `Integer` not null | rows consumed from the snapshot |

Constraints: primary key
`(tenant_id, definition_id, source_snapshot_id, source_schema_version)`;
`CHECK (sequence >= 0)`; `CHECK ((sequence = 0) = (after_source_id IS NULL))`.

The advance is one statement with the full previous tuple in the predicate, so a second runner
of the same page changes zero rows (`checkpoint_compare_and_set_conflict`) instead of forking
the cursor.

## 3. `m05_plugin_instance_import_quarantine` — original quarantine / conflict evidence

Append-only. A row is never updated except `attempt_count`.

| Column | Type | Notes |
|---|---|---|
| `id` | `String(40)` primary key | `m05q_<16 hex>` |
| `tenant_id`, `definition_id` | text not null | partition |
| `source_kind`, `source_id`, `source_schema_version` | text not null | exact source key |
| `source_digest` | `String(71)` not null | digest observed for this row |
| `original_digest` | `String(71)` null | digest of the mapping that was **not** overwritten |
| `outcome` | `String(16)` not null | `quarantined`, `conflict`, `blocked` |
| `reason_code` | `String(80)` not null | closed redacted code |
| `correlation_id` | `String(128)` not null | |
| `checkpoint` | `String(128)` not null | mapping checkpoint digest |
| `cursor_checkpoint` | `String(128)` not null | page cursor digest at observation time |
| `source_snapshot_id`, `source_schema_version_snapshot`, `source_snapshot_digest` | text not null | pinned lineage |
| `after_source_id`, `sequence` | text/`Integer` not null | mapping checkpoint fields |
| `attempt_count` | `Integer` not null default 0 | CAS counter for retries |

Constraints: unique `(tenant_id, definition_id, source_kind, source_id,
source_schema_version, source_digest, outcome, checkpoint)` named
`uq_m05_import_negative_evidence` (a re-observed identical negative row is idempotent; a changed
digest or checkpoint is a *new* row), `CHECK (outcome IN ('quarantined','conflict','blocked'))`,
`CHECK (attempt_count >= 0)`.

## 4. `m05_plugin_instance_quarantine_attempts` — contiguous immutable retry history

| Column | Type | Notes |
|---|---|---|
| `quarantine_id` | `String(40)` not null | FK to relation 3, `ON DELETE RESTRICT` |
| `attempt_number` | `Integer` not null | 1-based, contiguous |
| `outcome` | `String(16)` not null | same closed set as `ImportOutcome` |
| `reason_code` | `String(80)` null | null only for an accepted retry |
| `correlation_id` | `String(128)` not null | |
| `attempt_digest` | `String(71)` not null | deterministic digest of the full attempt |
| `source_digest` | `String(71)` not null | must equal the original quarantine digest |

Constraints: primary key `(quarantine_id, attempt_number)`; `CHECK (attempt_number >= 1)`.
There is no `UPDATE`/`DELETE` path in the adapter, so a retry can only append.

## 5. `m05_plugin_instance_runtime_audit` — redacted audit sink

| Column | Type | Notes |
|---|---|---|
| `event` | `String(80)` not null | e.g. `plugin_instance.import` |
| `outcome` | `String(24)` not null | `imported`, `replayed`, `quarantined`, `blocked`, `conflict`, `allowed`, `denied`, `stale-generation`, `advanced` |
| `reason_code` | `String(80)` not null | closed redacted code |
| `correlation_id` | `String(128)` not null | |
| `runtime_generation`, `writer_generation` | `Integer` null | non-negative when present |

There is intentionally **no** column for tenant, subject, session, scope, instance identity,
payload, token or credential. `CHECK (event <> '' AND outcome <> '' AND reason_code <> '' AND
correlation_id <> '')` and non-negative generations are the only content rules; a redaction
regression would require a schema change, which is a review event.

## 6. RLS expectations

Relations 1–5 are migration/security evidence. They must follow the M05 policy revision
(`2026110307`): `ENABLE`/`FORCE ROW LEVEL SECURITY`, a tenant `USING` and `WITH CHECK` policy on
`current_setting('app.tenant_id', true)`, and a trusted-service capability for the migration
writer path. The adapter refuses to run when its injected context does not report
`row_security_enabled`, `row_security_forced`, both policy directions, missing-context denial and
a non-bypassing role — it never emits a session-level `SET`.

## 7. Verification status

The relation shapes above are a contract for the next DDL revision, not installed schema. No
`alembic upgrade`, DDL, test run or database write was performed while authoring this file.
