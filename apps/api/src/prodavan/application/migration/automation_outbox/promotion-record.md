# MIG-AUTO-02 promotion record

- Record ID: `MIG-AUTO-02:blocked:preflight:2026-11-01` (superseded)
- Task: `MIG-AUTO-02`
- Status: `superseded by implementation-promotion-record.md; prior evidence remains historical`
- Scope: `exclusive automation_outbox application/test zones only`
- Partition: `none admitted; AUTO-01 ownership and persistence handoff not evidenced`
- Durable outbox transactions / delivery adapter / lease authority: `not found or not supplied`
- Acceptance fixtures: `authored, not executed`
- Compatibility projection: `contract only; no legacy schema/adapter verified`
- Canonical automation read/write owner: `unchanged; no partition promoted`
- `canonical_positive_writes`: `false`
- Promotion/cutover authorized: `false`
- Actor: `delegated implementation agent`
- Decision owner: `migration coordinator`
- Reviewer/signature: `pending`

## Deliverables

- Immutable, digest-bound outbox intent model, scoped to tenant/project/automation partition and source run/event, with receiver idempotency key.
- Repository port requires outbox intent to commit atomically with originating source state in the durable transaction; identical replay is idempotent and digest mismatch conflicts.
- Bounded delivery claim contract and outcome state model (`pending`, `claimed`, `delivered`, `dead_letter`) with lease, epoch, fencing token, attempt, retry time, and receiver receipt requirements.
- Worker service fails closed on missing/expired/mismatched lease and rejects malformed cross-partition claims; repository must CAS outcomes against current lease and attempt.
- Compatibility projection is a revision-CAS derived view, guarded by partition and current fencing token; it does not become canonical truth.
- Per-partition ownership state machine starts legacy read/write, permits shadow read with legacy sole writer, and requires an immutable partition-bound gate envelope plus server-side verifier for canonical promotion. The ownership repository CAS must atomically record transition and gate evidence; rollback is evidence-bearing and recovery requires a newer epoch.
- Acceptance fixtures and contract declarations cover crash, retries, duplicate fencing, projection replay, ownership transitions and fail-closed dependencies. They were written but not run.

## Blockers and gate

MIG-AUTO-01 ingress/task/run linkage and production persistence are upstream dependencies, but the handoff is not evidenced in this checkout. No durable adapter was located or supplied to prove atomic intent creation, uniqueness, claim leases, outcome CAS, or append-only transition evidence. No destination idempotency contract is available. This means external exactly-once delivery cannot be claimed: dispatch is at-least-once unless the receiver durably deduplicates `OutboxIntent.idempotency_key`. A crash after the receiver performs an effect but before local outcome commit must retry with that same key.

Fail closed: do not accept/promote a partition, do not enable canonical positive writes, and do not replace compatibility routes/projections. Adapter absence, missing AUTO-01 evidence, or missing lease authority must not fall back to process memory, broker dedupe, read-then-write, or legacy implicit ownership. Before promotion, provide and review the AUTO-01/persistence handoff; prove atomic transaction and replay behavior; prove exclusive claim and stale-worker fencing under concurrent lease takeover; validate receiver dedupe and receipt semantics; establish compatibility projection schema/owner and bounded parity/acceptance evidence; issue immutable gate envelope binding tenant/project/automation partition, parity digest, final checkpoint, evidence digest, decision owner and signature reference; verify it through a server-side verifier port; make repository CAS atomically persist owner transition plus append-only transition/gate evidence; run authored acceptance checks only after the migration coordinator explicitly lifts verification freeze; record per-partition approval and epoch. Missing verifier, rejected signature/evidence, partition mismatch, or CAS conflict must deny canonical promotion.

Promotion requires a superseding, signed decision record for exactly one partition. This preflight record grants no production write/read ownership and is not evidence of persistence readiness.

## Rollback

No partition was enabled and no durable intent, projection, or canonical state was written by this contract-only change; rollback is a no-op. Keep current route and owner unchanged. If a future partition is enabled, stop new claims for that partition, revoke/advance its lease epoch to fence in-flight workers, restore legacy read/write ownership, preserve immutable intents, attempts, receipts and delivery evidence, and reconcile uncertain external effects using the receiver idempotency key. Never delete acknowledged history or treat replay after timeout as proof the receiver did nothing.

## Freeze confirmation

Acceptance fixtures and contract declarations were authored and intentionally not executed. No tests, builds, compilation, lint, formatting, or formal git checks were run. No commit or push was made.
