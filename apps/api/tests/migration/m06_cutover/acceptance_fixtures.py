"""MIG-M06-03 proof-complete acceptance fixtures; intentionally unexecuted."""

from datetime import UTC, datetime, timedelta

from prodavan.application.migration.m06_cutover.cutover import (
    CanonicalRepairEvidence,
    EvidenceReference,
    FinalDeltaEvidence,
    GateEvidence,
    GateLineageEvidence,
    GateStage,
    LegacyOwnerEvidence,
    LegacyWriterDrainEvidence,
    PartitionKey,
    RollbackEvidence,
    WriterLease,
)

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
PARTITION = PartitionKey("tenant-a", "definition-a")
OTHER_PARTITION = PartitionKey("tenant-b", "definition-a")
SNAPSHOT = "sha256:" + "1" * 64


def digest(char: str) -> str:
    return "sha256:" + char * 64


def lease(epoch: int, *, partition: PartitionKey = PARTITION) -> WriterLease:
    return WriterLease(partition, f"cutover-{epoch}", epoch,
                       NOW + timedelta(minutes=10), f"fence-{epoch}")


def legacy_owner(epoch: int = 1) -> LegacyOwnerEvidence:
    return LegacyOwnerEvidence(
        PARTITION, "legacy-owner-1", digest("a"), SNAPSHOT, epoch,
        True, True, True, True,
    )


def gate(*, epoch: int = 3, partition: PartitionKey = PARTITION,
         active_legacy_writers: int = 0, source_sequence: int = 42,
         final_delta_checkpoint: str = "checkpoint-42",
         m04_parent_ok: bool = True,
         m04e_parent_ok: bool = True) -> GateEvidence:
    m04 = GateLineageEvidence(
        GateStage.M04, partition, "m04-evidence-1", digest("b"), SNAPSHOT,
        digest("c"), True,
    )
    m04e = GateLineageEvidence(
        GateStage.M04E, partition, "m04e-evidence-1", digest("d"), SNAPSHOT,
        digest("e"), True, m04.evidence_id if m04_parent_ok else "wrong-parent",
        m04.evidence_digest if m04e_parent_ok else digest("9"),
    )
    m05 = GateLineageEvidence(
        GateStage.M05, partition, "m05-evidence-1", digest("f"), SNAPSHOT,
        digest("a"), True, m04e.evidence_id, m04e.evidence_digest,
    )
    drain = LegacyWriterDrainEvidence(
        partition, "legacy-drain-1", digest("b"), SNAPSHOT, epoch, True,
        active_legacy_writers, 0, source_sequence,
    )
    delta = FinalDeltaEvidence(
        partition, "final-delta-1", digest("c"), SNAPSHOT, final_delta_checkpoint,
        digest("d"), epoch, source_sequence, True, drain.evidence_id, drain.evidence_digest,
    )
    return GateEvidence(
        partition=partition, report_digest=digest("e"), source_snapshot_digest=SNAPSHOT,
        final_delta_checkpoint=final_delta_checkpoint, writer_epoch=epoch,
        observation_count=12, audit_complete=True, parity_ready=True, handoff_verified=True,
        evidence_id="m06-evidence-1", evidence_digest=digest("f"),
        canonical_digest=digest("9"), verifier_ref="trusted-m06-verifier", m04=m04, m04e=m04e, m05=m05,
        parent_evidence_id=m05.evidence_id, parent_evidence_digest=m05.evidence_digest,
        legacy_owner_evidence_id="legacy-owner-1", legacy_owner_evidence_digest=digest("a"),
        legacy_writer_drain=drain, final_delta=delta,
    )


def rollback_evidence(epoch: int) -> RollbackEvidence:
    return RollbackEvidence(
        PARTITION, f"rollback-{epoch}", digest("e"), SNAPSHOT,
        "legacy-owner-1", digest("a"), epoch, True, True, 0, 0,
    )


def repair_evidence(epoch: int, parent_id: str, parent_digest: str) -> CanonicalRepairEvidence:
    refs = tuple(EvidenceReference(stage, f"{stage.lower()}-evidence-1", digest(char), PARTITION)
                 for stage, char in (("M04", "b"), ("M04E", "d"), ("M05", "f"), ("M06", "a")))
    return CanonicalRepairEvidence(
        PARTITION, f"repair-{epoch}", digest("c"), SNAPSHOT, digest("d"), epoch,
        parent_id, parent_digest, refs, 0, 0, 0, 0, 0, True, True,
    )


# Retained names used by the initial acceptance contract.
UNREADY_GATE = GateEvidence(PARTITION)
READY_GATE = gate()
LEASE = lease(3)
CASES = (
    "missing partition-bound M04 M04E M05 and M06 digests blocks positive transition",
    "upstream evidence must link by parent id and digest from M04 to M04E to M05 to M06",
    "final delta must follow legacy writer drain at the same epoch and frozen source sequence",
    "same command id and digest retries append no duplicate immutable lineage",
    "canonical promotion changes read decision and write owners atomically for one partition",
    "pre-read-cutover rollback requires fresh proof and restores one exclusive legacy owner",
    "post-read-cutover rollback is forbidden and recovery is canonical forward repair only",
)

