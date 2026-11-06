"""Append-only ports for audit integrity; implementations own transactions."""
from __future__ import annotations

from typing import Protocol

from .contracts import AuditOutcomeEvidence


class TrustedAuditAppendPort(Protocol):
    """Append only already-validated allow/deny evidence."""

    async def append_trusted(self, evidence: AuditOutcomeEvidence) -> None: ...


class DurableQuarantinePort(Protocol):
    """Durably record evidence that must never be treated as trusted success."""

    async def append_quarantine(self, evidence: AuditOutcomeEvidence, *, reason: str) -> None: ...


__all__ = ["DurableQuarantinePort", "TrustedAuditAppendPort"]
