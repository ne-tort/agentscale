# MIG-M04-02 implementation promotion record v3

Record ID: `MIG-M04-02:blocked:typed-mapping-active-resolution-v3:2026-11-03`
Supersedes: `MIG-M04-02:blocked:application-contract-v2:2026-11-02`
Status: `blocked`; typed mapping and active-resolution application contracts authored; no persistence adapter or source import.
Partition: `none admitted`.
Decision: legacy remains the only owner; do not backfill, enable canonical writes or resolve a scope positively until all evidence gates below are met.

## Inspected evidence

- Product contract: `docs/PRODUCT.md`; no change made.
- M04-01 foundation: `infrastructure/persistence/models/owner_registry.py` and `owner_registry/mig_m04_01_promotion_record.md`. Mapping uniqueness is `(source_kind, source_id, schema_version)`, source evidence width is 128, canonical provenance scope IDs are 40. Model registration/revision/RLS remain blocked as recorded by M04-01.
- Active access source: `CabinetGrantService`/`RelationsQuery` use active `cabinet_company_grants`; cabinet authorization also permits employee assignment or `company_grant_scope=all`, with company-owned owner proof. `MembershipRow` is unique by `(company_id, employee_id)` but has no status/deleted lifecycle. `ProjectAccessPolicy` applies soft-delete, cabinet access and restricted project assignment. Project `company_id` and `cabinet_id` are separate FKs; M04-01 adds a typed composite tuple contract but no installed key/RLS evidence was supplied.
- No source snapshot, transaction-local tenant context, exact-one membership proof, project inventory, reviewed RLS evidence or persistent M04 transaction adapter is available.

## Deliverables in this task zone

- `scope_migration.py`: deterministic `ScopeMappingInput`; source mapping key aligned to M04-01; canonical source digest; explicit resumable stream identity and checkpoint-load port; source mapping replay/conflict classifier; stable parent failure reason codes; redacted quarantine contract; source and canonical schema lineage checks; fail-closed transaction-bound active resolution; explicit plugin-instance deny; reviewed RLS and actor tenant-membership evidence requirements; strict coordinator validation of mapping/audit/quarantine result identities, original replay outcome, checkpoint position and returned persisted active row. No concrete adapter or route binding.
- `__init__.py`: task-local exports.
- `tests/migration/m04_scope/acceptance_fixtures.py`: typed hierarchy, source, checkpoint, transaction-bound parent-cardinality, reviewed RLS and actor-membership evidence fixtures; no storage fake.
- `tests/migration/m04_scope/test_acceptance_contract.py`: authored scenarios for deterministic mapping, idempotent replay, changed digest and schema identity, deleted/archived/ambiguous parents, membership and project tuple mismatch, cross-tenant assertions, archived/quarantined scopes, missing context/RLS/actor membership, plugin deny, redaction widths and checkpoint lineage. Not executed.
- `tests/migration/m04_scope/acceptance_matrix.md`: corrected source-key contract and inspected access-source gaps.

## Promotion evidence and blockers

- Snapshot ID/schema/digest, source counts, target counts/digest: `NOT_ACQUIRED`.
- Durable current checkpoint, mapping, audit and quarantine rows: `NOT_AVAILABLE`; contracts require an atomic transaction but no persistence owner exists in the exclusive zones.
- Exactly one active typed scope per source key: `NOT_PROVEN`.
- Active cabinet/company semantics: `NOT_PROVEN`; actual access code has multiple valid paths (active grant, employee assignment, company-owner relation, all-grant-scope). The positive M04 evidence contract specifically requires an exact active `cabinet_company_grants` tuple; no source snapshot established that this is the authorized migration basis for a partition.
- Employee/company membership lifecycle: `NOT_PROVEN`; current ORM relation has no lifecycle/status field.
- Project-company-cabinet tuple compatibility and deleted parent checks: `NOT_PROVEN` on live data.
- Model registration and Alembic migration: blocked outside the exclusive zones; no DDL was authored or run.
- RLS ENABLE/FORCE, USING/WITH CHECK, missing-context behavior, non-BYPASSRLS role and append-only mapping/quarantine/audit privileges: `ABSENT`.
- `ScopeRlsEvidence` is an application contract requiring same-transaction reviewed evidence. Its booleans cannot make installed RLS true; a trusted adapter must inspect actual policy/role state and bind the attestation to the DB transaction.
- Writer/fencing generation: `NOT_PROVISIONED`.
- Plugin-instance mapping/resolution: explicit deny/defer until M05 exact instance identity and reciprocal relation review.

## Gate decision and rollback

Promotion gate is `BLOCKED`. No rows were imported, no DB/environment state changed, no positive registry/grant/instance write is enabled, and legacy remains authoritative. Before a new promotion record, supply bounded source snapshot and digest; select and prove active tenant/cabinet membership semantics; verify exact live project-parent tuple and no deleted/ambiguous parents; register M04-01 metadata and review schema; implement the atomic idempotent checkpoint/mapping/quarantine/audit adapter; review RLS and restricted append-only roles; then reconcile counts, digests, audit and checkpoint evidence for the bounded partition. Preserve all first mappings and evidence on conflict; resume only against exact snapshot lineage. No rollback action is required for this contract-only work.

Verification freeze honored: tests, builds, compile, lint, format and git checks were intentionally not run. No DDL, live data, cutover, route changes, environment changes, commit or push occurred.