# ADR backlog: Mongo for module instance data (deferred)

**Status:** backlog — do **not** implement until Postgres copy-on-bind (`module_instances` / `module_instance_data_rows`) is stable in prod.

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
