# MIG-RUNTIME-API-01 — promotion record

Status: **BLOCKED / freeze retained**. This is an acceptance record, not production authorization.

## Scope reviewed

- API contracts and coordinator under `application/migration/runtime_api/`.
- Contract fixtures under `tests/migration/runtime_api/`.
- Source-of-truth behavior is `runtime_materialization/state_machine.py` and its typed ports.

## Contract disposition

- Plan/prepare/validate pin exact project + company + cabinet + Pod + resource scope, desired revision/digest, expected generation, immutable image digest, bridge-lease identity/generation/expiry, and trusted evidence.
- API terminal outcomes map one-to-one from state-machine `ApplyOutcome`; operation failures retain safe `operation_id`, required/optional status, and allow-listed reason codes. Raw adapter exceptions are not exposed.
- A positive/replay receipt carries the exact `AppliedRevision`; the API rejects a positive outcome without an atomic executor receipt. Atomic replay lookup uses idempotency key + plan digest before the generation-conflict gate. Stale new work remains conflict and cannot reach the executor.
- Unknown effects are `FAILED` with `reconciliation_required`; this is not a quarantine write and is never an applied result. This boundary defines neither quarantine persistence nor promotion.
- Existing project/Pod lifecycle and credential-lease HTTP endpoints are not substitutes for atomic runtime CAS. No route, DB/ORM, or Pod adapter was added.

## Acceptance coverage authored

- Outcome mapping and missing-executor-result rejection.
- Prepare scope/generation/image/lease checks and desired-revision pinning.
- Re-validate live lease/evidence and fence stale generation before executor.
- First apply, exact idempotent replay, no-op without generation bump, retry after failed state.
- Structured operation validation failures, redacted identifiers, unknown-effect reconciliation marker.
- Quarantine/promotion cannot be claimed from runtime outcome; promotion stays blocked.

## Promotion gate

**Do not promote or cut over.** There is no concrete `RuntimeApiAtomicExecutorPort`, durable runtime CAS/attempt adapter, bridge/image evidence adapter, or independently reviewed adapter integration in this task. The API contract can only acknowledge `APPLIED`/`ROLLED_BACK`/`REPLAYED` when a trusted atomic executor supplies a fully bound receipt after fenced effects and durable commit. Runtime quarantine and promotion remain separate, unimplemented evidence workflows.

Tests, builds, lint, formatting and Git checks are intentionally not run under the migration verification freeze. Promotion requires those checks after freeze lift, independent contract review, adapter proof, and explicit gate approval.
