"""Exclusive resource materialization evidence handoff boundary."""

from .contracts import (
    HandoffFailure,
    HandoffFailureCode,
    PromotionPartition,
    ResourceApplyResult,
    ResourceEvidenceHandoff,
    ResourceGrantEvidence,
    ResourceProvenance,
    RuntimeRevisionEvidence,
)
from .ports import ResourceApplyPort, ResourceEvidencePort
from .service import ResourceEvidenceHandoffService

__all__ = [
    "HandoffFailure",
    "HandoffFailureCode",
    "PromotionPartition",
    "ResourceApplyPort",
    "ResourceApplyResult",
    "ResourceEvidenceHandoff",
    "ResourceEvidenceHandoffService",
    "ResourceEvidencePort",
    "ResourceGrantEvidence",
    "ResourceProvenance",
    "RuntimeRevisionEvidence",
]
