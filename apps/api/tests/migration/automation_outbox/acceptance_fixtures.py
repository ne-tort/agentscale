"""MIG-AUTO-02 acceptance fixtures; intentionally not executed."""

from datetime import UTC, datetime, timedelta

from prodavan.application.migration.automation_outbox.outbox import (
    DeliveryOutcome,
    DeliveryState,
    OutboxIntent,
    OwnershipGateEvidence,
    PartitionKey,
    PartitionOwnership,
    ProjectionUpdate,
    WriterLease,
)

NOW = datetime(2026, 11, 1, 14, 40, tzinfo=UTC)
PARTITION = PartitionKey("tenant-fixture", "project-fixture", "automation-fixture")
LEASE = WriterLease(PARTITION, "worker-a", 7, "fence-7-a", NOW + timedelta(minutes=2))
INTENT = OutboxIntent(
    "intent-001", PARTITION, "run-001", "event-001", "automation.callback",
    "run-001:event-001:callback", {"result": "ok", "sequence": 3}, NOW,
)
DELIVERED = DeliveryOutcome(
    INTENT.intent_id, 1, LEASE.fencing_token, DeliveryState.DELIVERED,
    NOW + timedelta(seconds=2), receipt_id="receiver-receipt-1",
)
PROJECTION = ProjectionUpdate(
    PARTITION, INTENT.intent_id, INTENT.source_run_id, "legacy-task-001",
    {"status": "completed", "event_id": INTENT.event_id}, 4, LEASE.fencing_token,
)
INITIAL_OWNERSHIP = PartitionOwnership(PARTITION)
GATE_EVIDENCE = OwnershipGateEvidence(
    PARTITION, "promotion-evidence-01", "sha256:evidence-digest", "sha256:parity-digest",
    "checkpoint-42", "migration-coordinator", "signature://promotion/01", True,
)

CASES = (
    "source state transition and outbox intent commit or roll back atomically in durable storage",
    "intent replay with identical scoped key and digest returns the original immutable intent",
    "reused intent identity with changed digest conflicts and cannot overwrite payload or provenance",
    "due claim is atomic, bounded, increments attempt, and is partition-scoped",
    "live lease holder, monotonically increasing epoch and fencing token are stored on every claim",
    "expired lease cannot claim or record any result; stale worker completion after lease takeover is rejected",
    "retry transition requires a timezone-aware next_retry_at; retry delay/backoff and maximum attempts are adapter policy",
    "retryable failures return to pending; terminal/non-retryable failures move to dead-letter with immutable error evidence",
    "crash after send but before outcome commit retries with the same receiver idempotency key",
    "no-double-effect guarantee requires destination idempotency on intent.idempotency_key; transport alone is at-least-once",
    "delivered outcome requires a receiver receipt and an idempotent replay cannot create a second external effect",
    "compatibility projection is derived, revision-CAS updated, partition-scoped and fenced; it never becomes canonical source",
    "projection replay is idempotent; stale revisions/tokens and cross-partition identifiers are rejected",
    "initial ownership is legacy read/write; shadow reads canonical but legacy remains sole writer and projection is read-only",
    "canonical promotion requires immutable gate envelope with matching tenant/project/automation partition, parity digest, final checkpoint, decision owner and signature ref",
    "canonical promotion is denied when server-side verifier port is absent or rejects the envelope",
    "canonical ownership CAS atomically records transition evidence and gate evidence digest; stale revision, revoked gate or evidence conflict fails",
    "rollback returns one partition to legacy read/write, fences former canonical worker and retains accepted history",
    "rollback recovery requires a strictly newer lease epoch and explicit evidence; blocked state cannot promote implicitly",
    "missing transactional repository or lease authority is blocked and never falls back to memory or broker dedupe",
    "acceptance fixtures and promotion decision remain unexecuted/unapproved until migration coordinator opens gate",
)
