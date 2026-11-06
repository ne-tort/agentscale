"""Persistence-neutral append-only evidence ports for audit outcomes."""

from __future__ import annotations

from typing import Protocol

from .contracts import AuditOutcome


class AuditOutcomeAppendPort(Protocol):
    """Append-only sink; implementations must reject updates and replacements."""

    async def append(self, outcome: AuditOutcome) -> None: ...


class AuditOutcomeTransactionPort(Protocol):
    """Transaction evidence boundary without prescribing a database implementation."""

    async def append_transaction_evidence(self, outcome: AuditOutcome) -> None: ...


__all__ = ["AuditOutcomeAppendPort", "AuditOutcomeTransactionPort"]
