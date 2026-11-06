# MIG-M04E-01 foundation acceptance fixtures

Status: authored, not executed. This is static review input, not evidence that an upstream gate or historical row has passed.

## Fixture cases

| Fixture | Expected result | Contract evidence |
|---|---|---|
| definition owner key `(id, tenant_id)` | Unique; `tenant_id` references `companies.id` with `ON DELETE RESTRICT` and `String(40)` | `DynamicAppDefinitionFoundationRow` metadata |
| definition app key `(id, app_id)` | Unique target key for version app identity | `uq_m04e_definition_id_app` |
| definition tenant mismatch | Reject; never infer or rewrite tenant ownership | tenant FK and owner key |
| version tenant/app mismatch | Reject version whose mirrored tenant or app does not match its definition | composite version-to-definition FKs |
| orphan definition/version | Reject or quarantine; no positive resolution | named tenant/definition FKs and migration evidence policy |
| same manifest version in one definition | Reject duplicate | unique `(definition_id, manifest_version)` |
| same manifest version in distinct definitions | Allow distinct identities | uniqueness is definition-scoped |
| duplicate artifact digest in one definition | Reject duplicate immutable artifact identity | unique `(definition_id, artifact_digest)` |
| version linked to a different definition | Reject current pointer | composite current-version FK `(current_version_id, definition_id)` |
| invalid lifecycle, scope kind, or tenant scope identity | Reject | named lifecycle and scope checks |
| published definition without current version | Reject | `ck_m04e_definition_published_has_current` |
| version manifest identity/shape or requested capabilities disagree | Reject | named manifest and requested-capability checks |
| changed artifact digest or manifest through ORM | Reject update; preserve stored snapshot | immutable JSON values and `before_update` guard |
| direct SQL artifact/manifest update | Block promotion until a reviewed PostgreSQL trigger/privilege guard exists | ORM guard does not cover direct SQL or bulk DML |
| published version timestamp mismatch | Reject | named published lifecycle/timestamp checks |
| published definition points at non-published current version | Quarantine or reject in trusted cross-row validation; not proven by FK alone | staged lifecycle gate required |
| current-version cycle before both tables exist | Defer creation; emit as named late FK after both keys exist and validate before downstream use | `use_alter`, stable FK name, deferred transaction contract |
| absent/failed M04-01 owner registry gate | Keep foundation empty and promotion blocked | upstream gate evidence is absent |
| positive publication, instance, or grant write | Prohibited | this task creates no writer, grant, runtime path, backfill, or cutover |

## Explicit handoffs

- Model classes are already imported/exported by `models/__init__.py`; this task did not change package registration. That pre-existing registration is not evidence of upstream gate completion, DDL readiness, or owner approval.
- The immutable artifact digest requires a reviewed database-side UPDATE guard. Add it only in an authorized DDL revision; none is created or applied here.
- The current-version composite FK is staged with `use_alter=True`, a stable constraint name, and deferred validation. Review the generated operation order before creating any schema revision.
- Keep legacy state as the decision/read/write owner. Promotion outcome remains `blocked`, canonical positive writes remain false, and no migration inventory or source-data proof is claimed.

## Promotion and rollback boundary

Promotion requires independently recorded M04-01 typed-owner/RLS/deletion-boundary evidence, a deterministic source inventory, validated same-definition version links, immutable artifact enforcement, and zero unresolved required rows. This task does not satisfy those gates.

Before evidence import, remove only empty task-owned objects after dependency review. After import, stop the affected mapping path, preserve evidence and legacy authority, and repair forward; do not delete imported evidence or restore a dual decision owner.

## Verification freeze

Fixtures are not executed. Tests, builds, compilation, lint, formatting, Alembic, DDL, data migration, cutover, and git actions remain prohibited for this task.