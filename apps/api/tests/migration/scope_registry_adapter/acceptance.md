# MIG-M04-ADAPTERS-01 acceptance contract

Status: authored for review; tests intentionally not executed under migration verification freeze.

## Boundary

The adapter lives exclusively in `apps/api/src/prodavan/application/migration/scope_registry_adapter/**`; fixtures and test contracts live in `apps/api/tests/migration/scope_registry_adapter/**`. It imports typed M04 contracts and calls `ScopeRegistryService` plus `ScopeResolver` from MIG-M04-02. It has no SQLAlchemy session, ORM model, SQL, Alembic, source writer, quarantine writer, or cutover dependency. A future concrete repository/transaction binding must remain outside this zone and satisfy the ports without bypassing the checks.

A resolution call is one read-only transaction. It validates server-derived context, sets transaction-local tenant/cabinet/project/user/role values and validates a receipt for the exact transaction, then reads the accepted gate and one coherent owner-registry/parent-evidence snapshot. It clears context in `finally`; the transaction boundary must also reset its local settings when it exits. The M04-02 resolver requires exactly one active typed row and exact same-transaction parent evidence. Missing or contradictory persistence evidence returns a closed `blocked`, `deny`, `quarantined`, `deferred`, or `system_failure` outcome. No outcome enables canonical positive writes.

Tenant compatibility requires an exact retained active `companies.id` anchor. Cabinet compatibility requires that tenant, the exact cabinet, one active `cabinet_company_grants` membership, and matching owner-anchor evidence; nullable cabinet company mirrors are not accepted. Project compatibility requires exact project, tenant, and cabinet tuple plus active project policy evidence. No relation is inferred from copied IDs alone.

`plugin_instance` always returns `deferred` before opening a transaction or reading repositories. It is not a positive branch, even when a registry row exists. M05 exact-instance identity and reciprocal FK validation remain downstream prerequisites.

## Static acceptance scenarios

The unrun pytest contract covers:

- active tenant, cabinet, and project shape with exact evidence IDs, one transaction, and transaction-local context cleanup;
- missing adapter port, untrusted/missing context, cross-tenant assertion, missing/unaccepted M04 gates, malformed gate digests, incomplete gate/evidence IDs, and upstream positive-writer flag;
- non-transaction-local RLS receipts, failed context clear, wrong transaction evidence, and ambiguous registry cardinality;
- exact project/company/cabinet compatibility, archived registry owner state, and unresolved parent proof;
- changed source identity/digest, mapping digest, snapshot/schema/checkpoint lineage, and missing required evidence IDs;
- unconditional plugin deferral with no transaction or repository call;
- no raw payload, token, credential, or secret field in the evidence-ID contract;
- all returned outcomes retain `canonical_positive_writes = false`.

## Known runtime blockers

MIG-M04-01's current ORM module is imported by `models/__init__.py`; its promotion record still reports no source inventory/backfill, no Alembic revision or RLS/privilege validation, and no accepted database constraint gate. MIG-M04-02's latest promotion record is blocked, has no admitted partition, no active parent proof, no transaction-local RLS adapter, and no durable repository/checkpoint/audit binding. No source snapshot, accepted scope evidence, executable RLS review, or accepted partition gate was supplied. The new adapter therefore defines a port-driven contract and fails closed; it is not a production-wired database adapter and cannot return `active` until those external gates and bindings exist.

The acceptance fixture's accepted gate is synthetic and used only to describe the positive contract; it is not evidence that upstream migration gates passed.

## Freeze

Tests, builds, standalone lint, formatting, compilation, Alembic, DDL, database/live writes, cutover, environment changes, and git checks remain unrun/prohibited until an explicit coordinator decision lifts the verification freeze. The file-editing tool emitted automatic Python lint/syntax feedback during source and fixture writes; this was incidental tool behavior, not an explicit verification run.
