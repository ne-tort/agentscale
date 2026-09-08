# ADR backlog: Mongo for module instance data (deferred)

**Status:** backlog — do **not** implement until Postgres copy-on-bind (`module_instances` / `module_instance_data_rows`) is stable in prod.

## As-built (2026-09)

Still **Postgres JSONB** only for module instances. Modules and bindings stay in Postgres; instance meta/data are `module_instance_meta_documents` / `module_instance_data_rows`.

**Separate:** platform Document Store BC on Mongo is **accepted** for general app non-relational data — see [ADR-document-store-mongo.md](ADR-document-store-mongo.md). That BC does **not** replace this backlog item.

## Context

Module instance rows are Postgres JSONB today (evolution of cabinet `module_data_rows`). Mongo could hold large/flexible documents with `instance_id` pointers kept in Postgres.

## Decision (for now)

Stay on Postgres JSONB. Bind/fork/materialize and GitOps (Alembic) stay transactional with bindings.

## If revisited

- Overlay: Mongo StatefulSet + PVC + SealedSecret + backup
- CI Images + migrate job dual-write or cutover
- Pointer table in Postgres: `instance_id` → Mongo collection doc
- Rollback plan and ops validate

See PRODUCT.md § Module data model.
