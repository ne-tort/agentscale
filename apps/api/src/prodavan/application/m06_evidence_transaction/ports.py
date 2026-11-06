"""The only trusted persistence boundary for M06 evidence."""

from __future__ import annotations

from typing import Protocol

from .contracts import AuditOutcomeEvidenceV2, TransactionContext


class EvidenceTransactionPort(Protocol):
    """Implementations must commit the complete bundle or append nothing."""

    async def commit_evidence_transaction(
        self, *, evidence: AuditOutcomeEvidenceV2, context: TransactionContext
    ) -> str:
        """Atomically persist evidence, promotion, outcome, snapshot and audit."""
        ...


__all__ = ["EvidenceTransactionPort"]
