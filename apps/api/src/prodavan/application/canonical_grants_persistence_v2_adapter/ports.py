"""Persistence-neutral ports for trusted V2 audit append and quarantine."""
from __future__ import annotations

from typing import Protocol

from prodavan.application.audit_outcome_adapter import AuditTransactionEvidenceV2


class TrustedAuditTransactionPort(Protocol):
    """Append complete V2 evidence through the trusted transaction boundary."""

    async def append_trusted(self, evidence: AuditTransactionEvidenceV2) -> None: ...


class DurableQuarantinePort(Protocol):
    """Persist evidence excluded from trusted M06 authorization paths."""

    async def append_quarantine(self, evidence: object, *, reason: str) -> None: ...


__all__ = ["DurableQuarantinePort", "TrustedAuditTransactionPort"]
