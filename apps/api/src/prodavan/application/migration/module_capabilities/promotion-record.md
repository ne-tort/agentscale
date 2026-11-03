# MIG-MODULE-01 promotion record

- Task: `MIG-MODULE-01`
- Status: `blocked / contract-only`
- Exclusive scope: `apps/api/src/prodavan/application/migration/module_capabilities/**` and `apps/api/tests/migration/module_capabilities/**`
- Partition: `none admitted; no tenant/module owner partition was imported`
- Legacy owner: `modules`, `module_meta_documents`, existing module action/runtime/DSL zones remain authoritative and untouched
- Canonical positive writes: `false`
- API/runtime cutover: `false`
- Acceptance tests: `21` test functions across the task-owned acceptance suite, authored and intentionally not executed under verification freeze
- Formal checks/git checks: intentionally not run per task instruction

## Deliverables

- Typed immutable contracts for `CapabilityDefinition`, `CapabilityVersion`, schemas/fields, first-class schema/field references, views/navigation, typed actions, MCP servers/tools, materialization rules, seed declarations, dependencies/conflicts, owner scope, lifecycle evidence and published snapshots.
- `PublishedCapabilitySnapshot` binds the immutable artifact digest to the exact current semantic version and requires durable dependency-graph evidence digest in addition to owner/version/scope proof.
- Strict legacy normalizer for existing meta slugs. It requires stable schema/field/view/action/MCP IDs, resolves references to IDs, rejects unsupported shapes, rejects unsafe paths/inline secrets, and returns stable quarantine reason codes without writing legacy rows.
- MCP package manifest boundary with explicit server IDs, transport/endpoint checks, credential-header reference checks, and typed tool schema/implementation checks.
- Explicit lifecycle service for `draft -> published -> deprecated` with forward-only transition evidence. Publication is fail-closed until durable owner, version, scope, artifact immutability, writer generation and verifier gates pass.
- Application ports only: draft/quarantine persistence, immutable snapshot publication, transition append, and publication gate verification. No SQLAlchemy model, Alembic revision, API route or runtime integration was added.
- Dependency/conflict validator with missing dependency, non-published dependency, same-definition dependency, version-constraint mismatch, published conflict and cycle reason codes.
- Acceptance fixtures/tests for normalization, quarantine, canonical digest, dependency graph, gate denial, identity mismatch, immutable snapshot and lifecycle deprecation.

## Dependencies and blockers

1. Upstream M04 typed owner/scope registry and M04.e definition/version evidence are not supplied in this task zone. The service therefore accepts no positive publication without externally verified evidence.
2. No durable repository implementation, CAS/writer-fencing adapter, immutable snapshot store, quarantine/audit store or canonical schema exists here. Ports are deliberately unbound.
3. No reviewed mapping exists for every legacy action kind or MCP package source. Unsupported/ambiguous forms are quarantined; no runtime dispatch is inferred.
4. Existing module rows and runtime/DSL zones were not modified. This is a prerequisite for any later adapter review, not parity or cutover.
5. `MCP server` package source and tool implementation registry need a downstream owner/version/scope handoff before positive materialization.

## Gate and rollback

Promotion is denied until the coordinator supplies accepted durable owner/version/scope evidence, reviewed schema IDs and action/MCP operation mappings, immutable snapshot persistence with digest CAS, dependency/conflict graph evidence, audit/quarantine retention, and a server-side gate verifier. Tests/build/lint/compile/formal checks remain frozen.

Rollback is a no-op for this contract-only slice: no source rows were imported and no target state was written. Keep legacy module rows and existing runtime/DSL authority unchanged. If a later bounded partition is enabled, stop only that partition, preserve immutable snapshots/quarantine/transition evidence, fence its writer generation, and repair forward. Never rewrite a published snapshot or delete evidence to make a gate pass.

Corrections require a superseding immutable promotion record linked to this record; no destructive downgrade is authorized.
