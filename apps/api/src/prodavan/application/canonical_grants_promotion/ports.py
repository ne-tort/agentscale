"""Read and append-only evidence ports for M06 promotion."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .contracts import PromotionPartitionKey, PromotionRecord, QuarantineRecord, RedactedAuditOutcome


class PromotionRecordAppendPort(Protocol):
    """Append-only sink; it must reject updates/replacements for record_id."""

    async def append_promotion(self, record: PromotionRecord) -> None: ...


class PromotionRecordReadPort(Protocol):
    async def list_promotions(self, *, partition: PromotionPartitionKey) -> Sequence[PromotionRecord]: ...


class QuarantineAppendPort(Protocol):
    async def append_quarantine(self, record: QuarantineRecord) -> None: ...


class PromotionDecisionPort(Protocol):
    """Server-side decision evidence; never a database/ORM dependency."""

    async def evaluate_gates(
        self, *, partition: PromotionPartitionKey, evidence: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class PromotionEvidencePort(Protocol):
    """Read-only source/canonical evidence resolver."""

    async def read_evidence(self, *, partition: PromotionPartitionKey) -> Mapping[str, Any]: ...


class AuditOutcomeAppendPort(Protocol):
    async def append_audit_outcome(self, outcome: RedactedAuditOutcome) -> None: ...


__all__ = [
    "AuditOutcomeAppendPort", "PromotionDecisionPort", "PromotionEvidencePort",
    "PromotionRecordAppendPort", "PromotionRecordReadPort", "QuarantineAppendPort",
]
