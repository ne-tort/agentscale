"""Ports consumed by the decision-only audit outcome adapter."""

from __future__ import annotations

from typing import Protocol

from prodavan.application.canonical_grants_persistence_adapter import EvidenceAppendTransaction

from .contracts import AuditOutcomeEvidence, AuditTransactionEvidenceV2


class AuditOutcomeTransactionEvidencePort(Protocol):
    """Transaction seam accepting mapped evidence, without DB assumptions."""

    async def append_transaction_evidence(
        self, evidence: AuditOutcomeEvidence | AuditTransactionEvidenceV2
    ) -> None: ...


class CanonicalGrantsEvidencePort(EvidenceAppendTransaction, Protocol):
    """Canonical grants persistence evidence port used only for append-only audit."""


__all__ = ["AuditOutcomeTransactionEvidencePort", "CanonicalGrantsEvidencePort"]
