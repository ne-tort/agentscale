"""Contracts for the M06 trusted audit persistence V2 boundary."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from prodavan.application.audit_integrity import AuditOutcomeEvidence as LinkedAuditOutcomeEvidence
from prodavan.application.audit_outcome_adapter import AuditTransactionEvidenceV2
from prodavan.application.canonical_grants_promotion import RedactedAuditOutcome


class LegacyAuditDisposition(StrEnum):
    """Classification of a narrow legacy projection at the M06 boundary."""

    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class LegacyAuditClassification:
    """Explicitly non-trusted classification for a direct redacted append."""

    outcome: RedactedAuditOutcome
    disposition: LegacyAuditDisposition = LegacyAuditDisposition.QUARANTINED
    reason: str = "legacy_redacted_audit_outcome"

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("legacy classification reason is required")


TrustedAuditEvidenceV2 = AuditTransactionEvidenceV2
QuarantinedAuditEvidence = LinkedAuditOutcomeEvidence

__all__ = [
    "LegacyAuditClassification",
    "LegacyAuditDisposition",
    "QuarantinedAuditEvidence",
    "TrustedAuditEvidenceV2",
]
