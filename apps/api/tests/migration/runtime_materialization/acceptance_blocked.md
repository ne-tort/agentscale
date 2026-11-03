# MIG-RES-02 acceptance and promotion gate

Status: `blocked` / persistence adapter and acceptance specification authored. No artifact has been executed.

## Acceptance scenarios

1. Desired revisions are immutable; canonical digest binds resource key, revision, specification, provenance, and correlation.
2. Planner output is deterministic for identical input and emits a stable plan digest. Ordering and serialization use canonical sorted JSON.
3. No current applied revision plans generation 1; replacement increments exactly one; identical desired digest is a no-op/replay and never bumps generation.
4. The state-machine contract requires resource/digest binding, idempotency key, observed generation, and a durable atomic CAS. The SQL adapter currently has only conditional lifecycle-state CAS plus per-entry generation checks; it cannot prove resource-wide CAS or fence empty plans.
5. The state-machine contract requires replay to return the exact prior applied revision and conflict on changed plan content. The SQL adapter can replay the exact persisted plan only; there is no durable applied-attempt receipt.
6. Provenance remains attached from source kind/id/revision and correlation through desired and applied revisions. Applied record pins image by immutable `sha256:` digest.
7. Bridge lease identity and validity, projected-secret reference, image digest, agent run, approval, and event links are mandatory. Missing or untrusted evidence produces `BLOCKED` before side effects.
8. Failures in mandatory redacted audit prevent positive apply acknowledgement. Audit must not contain secret values, raw payload, or credential-bearing references.
9. Rollback is a separately approved command targeting an immutable prior applied revision. Execution must create a new monotonic generation; history is never rewritten and generation is never decremented.
10. `BLOCKED`, `CONFLICT`, stale lease, expired lease, and incomplete approval/event linkage never grant runtime authority.

## Blockers (fail closed)

- The registered ORM foundation now has a scoped SQLAlchemy adapter for desired revision/bundle and materialization-entry read/write, plan-level idempotency replay/conflict, per-entry generation CAS, and failed lifecycle persistence. It is deliberately not a full `RuntimeRevisionStorePort` and does not publish `ready`.
- No durable runtime attempt/receipt/fence table exists. `MaterializationEntryRow.generation` is per-path, cannot fence an empty plan, and unique revision/idempotency rows do not preserve multiple attempts. A revision with an existing plan is immutable; retry needs a new revision until attempt-history schema exists.
- No runtime attempt/audit ORM table or production trusted same-transaction audit writer exists. The adapter requires an injected session-bound audit port for mutations; only the test fixture currently demonstrates rollback when it fails.
- Runtime bridge lease issuer/subject binding, live expiry/revocation, projected-secret rotation, independent immutable image provenance, and authoritative `agent_runs`/approval/event resolution are still external gates. The adapter checks evidence fields and exact image binding, not those live proofs.
- No existing production caller wires the revision repository, `RuntimeMaterializationService`, or atomic executor. `project_runtime_reconcile` remains a read-only source consumer; API routes/Pod execution were not modified.
- Existing project runtime tables do not establish transaction-local tenant RLS context. The adapter filters exact company/project/cabinet/Pod, but policy and privilege evidence remain absent.
- Therefore dependency evidence is not production proof; promotion and operational rollback remain prohibited until upstream contracts, persistence, audit and caller wiring are accepted.

## Deliverables and promotion

The package supplies the pure deterministic state machine plus `SqlAlchemyRuntimeRevisionRepository` for the durable subset supported by registered ORM rows. The adapter supports scoped desired snapshot/bundle persistence and exact plan entries, plan replay/conflict, initial entry-generation CAS and terminal failed persistence. It does not implement the state-machine attempt store, durable receipt history, production audit, a Pod operation adapter, caller wiring, or a ready transition. The approved application rollback command remains a state-machine contract, not a production executor. No model, route, DDL, live database, or cutover change is included. Promotion requires the missing schemas/writers/proofs/call site, independent review and execution after the verification freeze is explicitly lifted.

## Rollback / gate

Gate: `BLOCKED` by default. Before promotion, rollback is permitted only through a separately approved command with a pinned target revision, current expected generation, valid dependency evidence, and an implementation that records a new generation. Until such executor and contract are approved, operational rollback must not be attempted through this package. Preserve legacy runtime as owner during the block.

Acceptance artifact only. Tests, builds, lint, formatting, compilation, and git checks were intentionally not run.
