"""Dependency ports for the exclusive audit integrity orchestration zone."""
from __future__ import annotations

from typing import Protocol

from prodavan.application.audit_integrity import AuditOutcomeEvidence
from prodavan.application.audit_outcome_adapter import (
    AuditOutcomeEvidence as AdaptedAuditOutcomeEvidence,
    AuditTransactionEvidenceV2,
)
from prodavan.application.audit_outcomes import AuditOutcome


class AuditIntegrityValidationPort(Protocol):
    """Validate linked auth/final events before any outcome route is selected."""

    @classmethod
    def validate(cls, outcome: AuditOutcome, *, auth_event, final_result_event) -> AuditOutcomeEvidence: ...


class AuditOutcomeTransactionPort(Protocol):
    """Append full allow/deny evidence through the transaction seam only."""

    async def append_transaction_evidence(
        self,
        outcome: AuditOutcome,
        *,
        integrity: AuditOutcomeEvidence | None = None,
    ) -> AdaptedAuditOutcomeEvidence | AuditTransactionEvidenceV2: ...


class DurableQuarantinePort(Protocol):
    """Durably quarantine validated evidence without trusted append semantics."""

    async def append_quarantine(self, evidence: AuditOutcomeEvidence, *, reason: str) -> None: ...


__all__ = [
    "AuditIntegrityValidationPort",
    "AuditOutcomeTransactionPort",
    "DurableQuarantinePort",
]
