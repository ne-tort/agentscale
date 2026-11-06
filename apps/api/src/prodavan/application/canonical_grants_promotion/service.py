"""M06 promotion evidence service with no persistence implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from .contracts import REQUIRED_GATE_KEYS, PromotionOutcome, PromotionPartitionKey, PromotionRecord, QuarantineRecord, RedactedAuditOutcome
from .ports import AuditOutcomeAppendPort, PromotionDecisionPort, PromotionEvidencePort, PromotionRecordAppendPort, PromotionRecordReadPort, QuarantineAppendPort


class CanonicalGrantsPromotionService:
    """Validate bounded promotion evidence and delegate append/read boundaries."""

    def __init__(
        self,
        *,
        promotions: PromotionRecordAppendPort,
        promotion_reads: PromotionRecordReadPort | None = None,
        quarantine: QuarantineAppendPort | None = None,
        audit: AuditOutcomeAppendPort | None = None,
        decisions: PromotionDecisionPort | None = None,
        evidence: PromotionEvidencePort | None = None,
    ) -> None:
        self._promotions = promotions
        self._promotion_reads = promotion_reads
        self._quarantine = quarantine
        self._audit = audit
        self._decisions = decisions
        self._evidence = evidence

    async def collect_pre_cutover(
        self,
        *,
        partition: PromotionPartitionKey,
        source_snapshot_id: str,
        source_schema_version: str,
        source_digest: str,
        canonical_digest: str,
        binding_digest: str,
        final_delta_checkpoint: str,
        gate_evidence: Mapping[str, Any],
        fencing_epoch: int,
        writer_generation: int,
        decision_owner: str,
        actor: str,
        decided_at: datetime | None = None,
    ) -> PromotionRecord:
        """Build fail-closed pre-cutover evidence; never enables positive writes."""
        if self._evidence is not None:
            observed = await self._evidence.read_evidence(partition=partition)
            merged = _merge_evidence(gate_evidence, observed)
        else:
            merged = dict(gate_evidence)
        missing_gates = {
            key for key in REQUIRED_GATE_KEYS if key not in merged
        }
        for key in missing_gates:
            merged[key] = {"passed": False, "reason": "missing"}
        if self._decisions is not None:
            evaluated = await self._decisions.evaluate_gates(partition=partition, evidence=merged)
            merged = _merge_evidence(merged, {"derived": evaluated})
        # A decision port may enrich evidence, but it cannot manufacture proof
        # for a gate that was absent from the source evidence or replace a failed
        # gate with a positive result.
        for key in missing_gates:
            merged[key] = {"passed": False, "reason": "missing"}
        outcome = PromotionOutcome.PROMOTABLE if all(_gate_passes(merged.get(key)) for key in REQUIRED_GATE_KEYS) else PromotionOutcome.BLOCKED
        return PromotionRecord(
            partition=partition,
            source_snapshot_id=source_snapshot_id,
            source_schema_version=source_schema_version,
            source_digest=source_digest,
            canonical_digest=canonical_digest,
            binding_digest=binding_digest,
            final_delta_checkpoint=final_delta_checkpoint,
            gate_evidence=merged,
            fencing_epoch=fencing_epoch,
            writer_generation=writer_generation,
            decision_owner=decision_owner,
            actor=actor,
            decided_at=decided_at or datetime.now(UTC),
            outcome=outcome,
            canonical_positive_writes=False,
        )

    async def append_promotion(self, record: PromotionRecord) -> None:
        """Append evidence only; positive writes remain false unless reviewed final."""
        await self._promotions.append_promotion(record)

    async def list_promotions(self, *, partition: PromotionPartitionKey) -> Sequence[PromotionRecord]:
        if self._promotion_reads is None:
            return ()
        return await self._promotion_reads.list_promotions(partition=partition)

    async def append_quarantine(self, record: QuarantineRecord) -> None:
        if self._quarantine is None:
            raise RuntimeError("quarantine append port is required")
        await self._quarantine.append_quarantine(record)

    async def append_audit_outcome(self, outcome: RedactedAuditOutcome) -> None:
        if self._audit is None:
            raise RuntimeError("audit append port is required")
        await self._audit.append_audit_outcome(outcome)

    @staticmethod
    def reviewed_final(record: PromotionRecord, *, reviewer: str, actor: str) -> PromotionRecord:
        """Create the explicit final enable decision from immutable prior evidence."""
        if record.outcome not in {PromotionOutcome.PROMOTABLE, PromotionOutcome.PERSISTENCE_READY}:
            raise ValueError("only a promotable or persistence_ready record may become canonical owner")
        if not all(_gate_passes(record.gate_evidence.get(key)) for key in REQUIRED_GATE_KEYS):
            raise ValueError("reviewed_final requires every required gate to pass")
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or not isinstance(actor, str)
            or not actor.strip()
        ):
            raise ValueError("reviewer and actor are required")
        return PromotionRecord(
            partition=record.partition,
            source_snapshot_id=record.source_snapshot_id,
            source_schema_version=record.source_schema_version,
            source_digest=record.source_digest,
            canonical_digest=record.canonical_digest,
            binding_digest=record.binding_digest,
            final_delta_checkpoint=record.final_delta_checkpoint,
            gate_evidence=record.gate_evidence,
            fencing_epoch=record.fencing_epoch,
            writer_generation=record.writer_generation,
            decision_owner=record.decision_owner,
            actor=actor,
            decided_at=datetime.now(UTC),
            outcome=PromotionOutcome.CANONICAL_OWNER,
            canonical_positive_writes=True,
            reviewed_final=True,
            reviewer=reviewer,
            parent_record_id=record.record_id,
            lineage_reference=record.correlation_id,
        )


def _merge_evidence(
    existing: Mapping[str, Any], additions: Mapping[str, Any]
) -> dict[str, Any]:
    """Add independent evidence without replacing an existing gate result."""
    merged = dict(existing)
    for key, value in additions.items():
        if key not in merged:
            merged[key] = value
            continue
        if _gate_failed(merged[key]):
            continue
        if _gate_failed(value):
            merged[key] = value
    return merged


def _gate_failed(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("passed") is False


def _gate_passes(value: Any) -> bool:
    """Accept explicit positive evidence only; strings like ``ok`` are not proof."""
    if value is True:
        return True
    if isinstance(value, Mapping):
        return value.get("passed") is True
    return False


__all__ = ["CanonicalGrantsPromotionService"]
