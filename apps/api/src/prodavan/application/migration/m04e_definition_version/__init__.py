"""MIG-M04E-02 evidence backfill and M05 handoff contracts.

These contracts intentionally remain evidence-only. They cannot enable definition,
instance, or grant writes and fail closed until M04/M04E-01 gates are evidenced.
"""

from .backfill import (
    BackfillBatch,
    BackfillCheckpoint,
    BackfillOutcome,
    BackfillRecord,
    DefinitionVersionEvidenceBackfill,
    EvidenceCandidate,
    EvidenceStatus,
    HandoffGate,
    HandoffStatus,
    M05EvidenceExport,
    PromotionRecord,
)
from .repository import ServerDefinitionVersionRepository

__all__ = [
    "BackfillBatch",
    "BackfillCheckpoint",
    "BackfillOutcome",
    "BackfillRecord",
    "DefinitionVersionEvidenceBackfill",
    "EvidenceCandidate",
    "EvidenceStatus",
    "HandoffGate",
    "HandoffStatus",
    "M05EvidenceExport",
    "PromotionRecord",
    "ServerDefinitionVersionRepository",
]
