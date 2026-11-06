# MIG-RES-01 immutable promotion record

Record ID: `MIG-RES-01:blocked:preflight:2026-11-01`
Status: `blocked`
Partition: `all resource scopes / none admitted`
Decision: `do not promote; do not import or enable positive resolution`

## Evidence

- Scope taxonomy implemented as contracts: `tenant`, `session`, `project`.
- Resource lifecycle and typed versions implemented in this task zone; version history is append-only by contract.
- Binding is explicit and pins one resource version and typed scope.
- Secret-bearing values are rejected from payloads; credentials are represented by provider/locator/version references only.
- Canonical digest is computed inside this boundary from the source envelope, source identity, full resource/version content, and binding; mismatches quarantine before persistence.
- Import requires one trusted atomic identity+digest CAS port; no adapter exists here, so imports remain `blocked` and conflict preservation is only a required port contract.
- Resolution requires one immutable binding/resource/exact-version/scope-proof snapshot with a single generation; no trusted snapshot adapter exists here, so positive resolution remains disabled.
- M04 scope resolver: `absent / blocked`; runtime scope gate defaults closed.
- Source inventory, target persistence, RLS review, uniqueness constraints, transaction-local identity evidence, and CAS/snapshot adapters: `not provided`.
- Acceptance fixtures: authored, intentionally not executed.

## Promotion gate

No promotion is authorized. M04 must provide an accepted tenant and typed-anchor foundation, active parent/membership proof for session/project scopes, trusted transaction-local identity, and reviewed RLS. A trusted persistence adapter must implement one transaction that recomputes/accepts canonical identity+digest CAS, preserves the original mapping on conflict, and atomically writes resource/version/binding/evidence with required uniqueness and immutable version history. Resolution additionally requires a single tenant-scoped immutable snapshot of binding, resource, exact version, scope proof and generation, with a generation CAS at the application boundary before any side effect. Until those adapters and dependencies exist, both import and resolution remain fail-closed. Run the authored fixtures and complete migration-level review only after the coordinator explicitly releases the verification freeze.

## Rollback

No target data was imported. Keep legacy as the authority and do not activate the resolver. If a future bounded import is promoted and later fails, stop only that partition, disable its bindings, preserve import mappings/quarantine/audit evidence, and supersede this record with a linked immutable record; do not rewrite or delete historical resource versions.

This is a blocked preflight record, not production promotion, schema readiness, import completion, or authorization eligibility. Append-only; corrections require a superseding record linked to this ID.
