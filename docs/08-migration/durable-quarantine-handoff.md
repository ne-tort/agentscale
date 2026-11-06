# Durable quarantine handoff

Статус: migration-stage application contract, exclusive quarantine boundary. Baseline: 2026-11-01.

Этот handoff закрывает consistency для durable quarantine. Реализованы только application contracts, ports, fail-closed service и source acceptance tests в [`durable_quarantine`](../../apps/api/src/prodavan/application/durable_quarantine/). Здесь нет DB/ORM/Alembic, HTTP routes, trusted repository implementation или formal verification.

## 1. Canonical record

`QuarantineRecord` является канонической записью unsafe/unresolved migration outcome. Она immutable (`frozen` dataclasses) и содержит:

- `partition`: tenant-scoped `tenant_id` и стабильный `partition_key`;
- `identity`: полный tuple `tenant_id`, definition/app/version/manifest, typed scope, explicit subject, optional plugin instance/session/runtime and writer generations, capability key;
- `reason`: закрытый `QuarantineReason`, включая cross-tenant, missing identity, digest conflict, unsupported mapping, duplicate identity и invalid provenance;
- `source_digest`, `canonical_digest`, `binding_digest`: обязательные `sha256:` digests;
- `promotion_lineage`: source kind/id, checkpoint, attempt и optional parent record; repair is forward-only;
- `correlation_id`, `trace_id`, `span_id`;
- `evidence`: immutable redacted snapshot с evidence id and timezone-aware capture time;
- `record_id`, schema version, creation time and scoped `idempotency_key`.

`partition.tenant_id` обязан совпадать с `identity.tenant_id`. Empty or partial identity is rejected. Evidence stores only non-empty redacted strings; raw payloads, secrets and tokens are outside this contract.

## 2. Duplicate idempotency

`DurableQuarantineRepository` exposes only:

```text
find_by_idempotency_key(tenant_id, idempotency_key)
put_if_absent(record)
```

`DurableQuarantineService.record` first compares an existing record in the same tenant scope. An identical immutable fingerprint returns the original record and does not create a duplicate. The same scoped key with a changed source, canonical, binding, reason or source identity raises `QuarantineDuplicateConflict`; it is never overwritten and never silently skipped. Repository implementations must make `put_if_absent` atomic under their own future persistence transaction.

The idempotency key is not global across tenants: a key from another tenant must not be visible or matchable. Storage adapters must preserve tenant scope in both lookup and uniqueness.

## 3. Fail-closed and promotion rules

Quarantine is evidence, not a decision to allow. This zone has no `authorize`, `promote`, `grant`, trusted append, or success status operation. A quarantined row must never be read by authorization and must never count as positive parity. A later promotion, if approved by a separate migration boundary, must create a new forward record linked through `PromotionLineage`; it cannot mutate or delete the quarantine evidence.

Every retry keeps the source identity and compares all three digests again. A changed digest is a conflict. Missing/ambiguous parent identity, cross-tenant resolution, artifact or binding mismatch, unsupported mapping, duplicate active identity, stale generation, denied/expired provenance without proof, and any broadening transformation belong in quarantine rather than a positive path.

Correlation and trace fields are mandatory so that a retry, audit event and promotion checkpoint can be joined without trusting request payload assertions. `ImmutableEvidence` is copied into a read-only mapping at construction time, preventing post-construction evidence mutation.

## 4. Port and implementation boundary

The application service depends only on `DurableQuarantineRepository`. A future adapter may implement durable storage, append-only policy, RLS and transactionality, but those concerns are deliberately outside this migration handoff. No route, ORM model, Alembic revision, database session, or direct positive authorization write may be added under this module.

The source tests document acceptance behavior without being executed during the migration verification freeze. Formal tests, lint, builds, compilation and git checks remain intentionally deferred until the migration-wide review explicitly lifts the freeze.
