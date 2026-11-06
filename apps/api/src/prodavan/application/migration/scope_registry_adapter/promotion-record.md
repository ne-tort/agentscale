# MIG-M04-ADAPTERS-01 blocked promotion record

Record ID: `MIG-M04-ADAPTERS-01:blocked:application-contract-v1:2026-11-02`
Status: `blocked`; adapter contract and static acceptance scenarios authored only.
Partition: `none admitted`.
Decision: legacy remains authoritative; no active scope promotion, canonical positive writer, or cutover.

## Boundary and deliverables

Exclusive implementation zone: `apps/api/src/prodavan/application/migration/scope_registry_adapter/**`.
Exclusive acceptance zone: `apps/api/tests/migration/scope_registry_adapter/**`.

The application adapter binds the existing M04-02 `ScopeRegistryService` and `ScopeResolver` to injected transaction, gate and owner-registry repository ports. Reads, gate evidence, context setting, parent proof and scope resolution are bound to one transaction identity. It returns explicit fail-closed outcomes, exact supporting registry/mapping/parent evidence IDs, source and partition digests, snapshot/schema identity, and the checkpoint ID; every result retains `canonical_positive_writes=false`. It short-circuits plugin-instance scope as `deferred` before opening the transaction.

M04-01 ORM classes inspected: `CanonicalTenantAnchorRow`, `ScopeOwnerCabinetAnchorRow`, `ScopeOwnerProjectAnchorRow`, `ScopeRegistryRow`, mapping, quarantine and audit models in `owner_registry.py`. The contract assumes a repository translates these and source parents into typed evidence; no ORM imports, registration, model/DDL change, SQL, or concrete DB binding was added in this application zone.

## Gate evidence

- MIG-M04-01 model foundation: ORM declarations are imported through the current model package, but source inventory/backfill is blocked; no Alembic revision, RLS/privilege validation, or database constraint validation is evidenced.
- MIG-M04-02: latest application promotion record reports `blocked`, no admitted partition, no source snapshot, no exact active tenant/cabinet/project membership proof, and no trusted transaction/repository/checkpoint/audit adapter.
- Source snapshot/digest and canonical scope digest for a real partition: `NOT_AVAILABLE`.
- Accepted M04-01/02 immutable gate IDs, checkpoint and RLS/audit review evidence: `NOT_PROVIDED`.
- Writer generation/fencing: `NOT_PROVISIONED`.
- Active scope resolution through a concrete production binding: `BLOCKED`.
- Fixture gate IDs and parent evidence are synthetic test inputs only and do not change the gate status.

## Promotion and rollback

Promotion gate: `BLOCKED`. Do not admit a partition until upstream M04-01 and M04-02 records are accepted for the exact bounded tenant, source and canonical digests/checkpoint exist, tenant/cabinet/project parent and membership evidence is exact and non-ambiguous, the concrete repository proves same-transaction transaction-local RLS and append-only audit/cleanup, and the migration coordinator reviews the adapter binding. A green application fixture alone never makes the partition promotable. Plugin-instance resolution also remains deferred until M05 exact instance identity and reciprocal registry constraints are validated.

No source or database rows were written, so rollback is a no-op. If a later bounded read/import workflow fails, halt that partition, retain mapping/quarantine/audit/checkpoint evidence and legacy ownership, then repair forward. Never switch to dual ownership, truncate or rewrite scope identities, delete evidence, enable plugin scope, or cut over as fallback.

## Verification freeze

Acceptance tests are authored but intentionally not executed. No test/build/compile/format, standalone lint, formal or git checks, DDL/Alembic, live reads/writes, backfill, environment change, commit/push, or cutover was run. The file-writing/editing tool emitted automatic Python lint/syntax feedback while creating/editing source and fixture files; this was not an explicitly invoked verification command and is disclosed as incidental tooling behavior under the freeze.
