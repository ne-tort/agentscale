"""MIG-M06-03 acceptance fixtures; specifications are intentionally unexecuted during freeze."""

from datetime import UTC, datetime, timedelta

from prodavan.application.migration.m06_cutover.cutover import (
    ParityGate,
    PartitionKey,
    WriterLease,
)

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
PARTITION = PartitionKey("tenant-a", "definition-a")
OTHER_PARTITION = PartitionKey("tenant-b", "definition-a")
UNREADY_GATE = ParityGate(PARTITION)
READY_GATE = ParityGate(
    partition=PARTITION,
    report_digest="sha256:" + "1" * 64,
    source_snapshot_digest="sha256:" + "2" * 64,
    final_delta_checkpoint="checkpoint-42",
    writer_epoch=8,
    observation_count=12,
    audit_complete=True,
    parity_ready=True,
    handoff_verified=True,
    evidence_id="gate-evidence-42",
    evidence_digest="sha256:" + "3" * 64,
    verifier_ref="m06-02-verifier-42",
)
LEASE = WriterLease(PARTITION, "cutover-operator", 8, NOW + timedelta(minutes=5), "fence-8")

CASES = (
    "missing M06-02 parity report blocks promotion and leaves legacy read owner unchanged",
    "unverified handoff or any absent digest/checkpoint/observation/audit blocks fail-closed",
    "unexplained mismatch, unresolved positive binding, unknown mapping, or stale generation blocks",
    "lease expiry, foreign partition lease, stale epoch, or missing fencing token rejects promotion",
    "accepted promotion atomically changes canonical read owner and sets legacy projection read-only",
    "decision owner remains legacy until separate explicit decision-plane migration",
    "only selected tenant and app_definition partition changes; neighboring partitions remain unchanged",
    "same command id replay does not duplicate immutable evidence; cross-partition id reuse conflicts",
    "concurrent revision change fails CAS and requires fresh command evidence rather than overwriting state",
    "rollback requires fresh partition lease and restores legacy read owner while retaining promotion evidence",
    "re-promotion after rollback requires strictly advanced writer epoch",
    "evidence is append-only, digest-bound, redacted of payloads/secrets, and never mutated or deleted",
    "command and observation resource bounds reject oversized request and bounded M06-02 report",
)
