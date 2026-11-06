"""Persistence protocols for M06 trusted evidence.

Implementations belong to a later infrastructure layer.  The protocols expose
only exact partition reads and append-only evidence writes; no update, delete,
ORM session, or canonical grant writer is part of this boundary.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from prodavan.application.canonical_grants_promotion.contracts import (
    PromotionPartitionKey,
    PromotionRecord,
    QuarantineRecord,
    RedactedAuditOutcome,
)


class TrustedEvidenceRepository(Protocol):
    """Read trusted evidence and append immutable records in one future txn."""

    async def read_promotions(
        self, *, partition: PromotionPartitionKey
    ) -> Sequence[PromotionRecord]: ...

    async def read_quarantine(
        self, *, partition: PromotionPartitionKey
    ) -> Sequence[QuarantineRecord]: ...

    async def append_evidence(
        self,
        *,
        promotion: PromotionRecord | None = None,
        quarantine: QuarantineRecord | None = None,
        audit: RedactedAuditOutcome | None = None,
    ) -> None: ...


class EvidenceAppendTransaction(Protocol):
    """Optional transaction capability used by future repository adapters."""

    async def append_evidence(
        self,
        *,
        promotion: PromotionRecord | None = None,
        quarantine: QuarantineRecord | None = None,
        audit: RedactedAuditOutcome | None = None,
    ) -> None: ...


__all__ = ["EvidenceAppendTransaction", "TrustedEvidenceRepository"]
