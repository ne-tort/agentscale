"""Contracts for the decision-only audit outcome integration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from prodavan.application.audit_integrity import AuditOutcomeEvidence as LinkedAuditOutcomeEvidence
from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult
from prodavan.application.canonical_grants_promotion import RedactedAuditOutcome


@dataclass(frozen=True, slots=True)
class AuditOutcomeEvidence:
    """Validated outcome plus its append-only, redacted persistence mapping."""

    outcome: AuditOutcome
    redacted: RedactedAuditOutcome

    @property
    def decision(self) -> AuditDecision:
        return self.outcome.decision

    @property
    def result_status(self) -> AuditResult:
        return self.outcome.result_status

    def __post_init__(self) -> None:
        if self.redacted.correlation_id != self.outcome.correlation_id:
            raise ValueError("redacted audit correlation does not match outcome")


@dataclass(frozen=True, slots=True)
class AuditTransactionEvidenceV2:
    """Versioned trusted transaction envelope preserving linked audit evidence.

    ``legacy`` keeps the existing outcome adapter contract available to canonical
    persistence. ``integrity`` carries auth/final linkage, scope, IDs, digests,
    and redaction through the same transaction boundary without DB assumptions.
    """

    VERSION: ClassVar[str] = "2"

    legacy: AuditOutcomeEvidence
    integrity: LinkedAuditOutcomeEvidence

    def __post_init__(self) -> None:
        if self.integrity.outcome is not self.legacy.outcome:
            raise ValueError("transaction evidence must preserve the same outcome")
        if self.integrity.decision is AuditDecision.QUARANTINE:
            raise ValueError("quarantine evidence cannot enter trusted transaction")

    @property
    def outcome(self) -> AuditOutcome:
        return self.integrity.outcome

    @property
    def redacted(self) -> RedactedAuditOutcome:
        return self.legacy.redacted

    @property
    def auth_event(self):
        return self.integrity.auth_event

    @property
    def final_result_event(self):
        return self.integrity.final_result_event

    @property
    def scope(self) -> tuple[str, str]:
        return self.integrity.scope_kind, self.integrity.scope_id

    @property
    def event_ids(self) -> tuple[str, str]:
        return self.integrity.event_ids


__all__ = ["AuditOutcomeEvidence", "AuditTransactionEvidenceV2"]
