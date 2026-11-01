# MIG-RES-02 acceptance and promotion gate

Status: `blocked` / acceptance specification only. These artifacts have not been executed.

## Acceptance scenarios

1. Desired revisions are immutable; canonical digest binds resource key, revision, specification, provenance, and correlation.
2. Planner output is deterministic for identical input and emits a stable plan digest. Ordering and serialization use canonical sorted JSON.
3. No current applied revision plans generation 1; replacement increments exactly one; identical desired digest is a no-op/replay and never bumps generation.
4. Apply verifies resource/digest binding, idempotency key, observed generation, and atomic database-side CAS. Concurrent writers yield at most one successful generation transition.
5. Repeating the same idempotency key and plan returns the exact prior applied revision. Reusing a key with different plan content conflicts.
6. Provenance remains attached from source kind/id/revision and correlation through desired and applied revisions. Applied record pins image by immutable `sha256:` digest.
7. Bridge lease identity and validity, projected-secret reference, image digest, agent run, approval, and event links are mandatory. Missing or untrusted evidence produces `BLOCKED` before side effects.
8. Failures in mandatory redacted audit prevent positive apply acknowledgement. Audit must not contain secret values, raw payload, or credential-bearing references.
9. Rollback is a separately approved command targeting an immutable prior applied revision. Execution must create a new monotonic generation; history is never rewritten and generation is never decremented.
10. `BLOCKED`, `CONFLICT`, stale lease, expired lease, and incomplete approval/event linkage never grant runtime authority.

## Blockers (fail closed)

- Resource ownership/schema and desired/applied persistence contracts are incomplete; no production adapter or atomic transaction boundary is defined.
- Bridge lease contract (issuer, subject binding, expiry, revocation and renewal semantics) is incomplete.
- Projected-secret contract must define opaque reference provenance, rotation/version behavior, and proof that secret values never enter plans/audit. This package stores references only.
- Image contract must provide verified immutable digest and provenance; tags are not acceptable.
- `agent_runs`, approval, and event linkage contracts (authoritative identifiers, lifecycle, consistency and replay semantics) are incomplete.
- Redacted audit port, event taxonomy, retention, and failure semantics are not wired.
- No authorized rollback executor, restore preconditions, or resource-specific compensation contract exists. `RollbackCommand` is a validation/specification artifact, not an executable command.
- Consequently `contracts_ready` must remain false until upstream owners supply and approve those contracts; production promotion is prohibited.

## Deliverables and promotion

The application package supplies immutable contract models, deterministic planner, idempotent/CAS apply orchestration, generation rules, provenance, evidence gates, and rollback command validation. Acceptance fixtures define required adapter-level race, replay, stale evidence, audit, and rollback behavior. No adapters, CLI wiring, schema changes, or production authority are introduced. Promotion requires upstream contract completion, adapter implementation, independent review, execution of this acceptance suite after the migration verification freeze is lifted, and explicit gate approval.

## Rollback / gate

Gate: `BLOCKED` by default. Before promotion, rollback is permitted only through a separately approved command with a pinned target revision, current expected generation, valid dependency evidence, and an implementation that records a new generation. Until such executor and contract are approved, operational rollback must not be attempted through this package. Preserve legacy runtime as owner during the block.

Acceptance artifact only. Tests, builds, lint, formatting, compilation, and git checks were intentionally not run.
