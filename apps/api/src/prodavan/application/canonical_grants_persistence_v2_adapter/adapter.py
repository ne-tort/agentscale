"""Fail-closed V2 audit persistence adapter with an explicit legacy quarantine route."""
from __future__ import annotations

from datetime import UTC

from prodavan.application.audit_integrity import AuditIntegrityService, AuditOutcomeEvidence
from prodavan.application.audit_outcome_adapter import AuditTransactionEvidenceV2
from prodavan.application.audit_outcomes import AuditDecision
from prodavan.application.canonical_grants_promotion import RedactedAuditOutcome

from .contracts import LegacyAuditClassification
from .ports import DurableQuarantinePort, TrustedAuditTransactionPort


class CanonicalGrantsPersistenceV2Adapter:
    """Route only complete V2 evidence to trusted persistence.

    A narrow ``RedactedAuditOutcome`` has no M06 identity and is therefore
    classified as legacy quarantine evidence.  Quarantine never calls the
    trusted transaction port.
    """

    def __init__(
        self,
        *,
        trusted: TrustedAuditTransactionPort | None = None,
        quarantine: DurableQuarantinePort | None = None,
    ) -> None:
        if trusted is None and quarantine is None:
            raise ValueError("a trusted or quarantine port is required")
        self._trusted = trusted
        self._quarantine = quarantine

    async def append_trusted(self, evidence: AuditTransactionEvidenceV2) -> None:
        """Append only V2 ALLOW/DENY evidence to the trusted transaction port."""
        self._validate_trusted(evidence)
        if self._trusted is None:
            raise RuntimeError("trusted audit transaction port is required")
        await self._trusted.append_trusted(evidence)

    async def append(self, evidence: object) -> AuditTransactionEvidenceV2 | object:
        """Dispatch V2 to trusted append and all other supported evidence to quarantine."""
        if type(evidence) is AuditTransactionEvidenceV2:
            await self.append_trusted(evidence)
            return evidence
        if isinstance(evidence, RedactedAuditOutcome):
            classification = LegacyAuditClassification(evidence)
            await self._append_legacy_quarantine(classification)
            return classification
        if isinstance(evidence, AuditOutcomeEvidence):
            if evidence.decision is not AuditDecision.QUARANTINE:
                raise TypeError("trusted append requires AuditTransactionEvidenceV2")
            AuditIntegrityService._validate(evidence)
            await self.append_quarantine(evidence, reason=evidence.outcome.reason_code)
            return evidence
        raise TypeError("trusted append requires AuditTransactionEvidenceV2")

    async def append_quarantine(self, evidence: object, *, reason: str) -> None:
        """Send quarantine evidence only to the durable quarantine port."""
        if self._quarantine is None:
            raise RuntimeError("durable quarantine port is required")
        if not reason.strip():
            raise ValueError("quarantine reason is required")
        await self._quarantine.append_quarantine(evidence, reason=reason)

    async def _append_legacy_quarantine(self, classification: LegacyAuditClassification) -> None:
        await self.append_quarantine(classification, reason=classification.reason)

    @staticmethod
    def _validate_trusted(evidence: AuditTransactionEvidenceV2) -> None:
        if type(evidence) is not AuditTransactionEvidenceV2:
            raise TypeError("trusted append accepts only AuditTransactionEvidenceV2")
        if evidence.VERSION != "2":
            raise ValueError("unsupported audit transaction evidence version")
        if evidence.decision not in (AuditDecision.ALLOW, AuditDecision.DENY):
            raise ValueError("trusted audit append accepts only allow or deny")
        outcome = evidence.outcome
        redacted = evidence.redacted
        AuditIntegrityService._validate(evidence.integrity)
        if redacted.correlation_id != outcome.correlation_id:
            raise ValueError("redacted correlation does not match full outcome")
        if redacted.mcp_trace_id != outcome.trace_id or redacted.span_id != outcome.span_id:
            raise ValueError("redacted trace identity does not match full outcome")
        if redacted.profile_id != outcome.profile_id or redacted.tool_server != outcome.tool_server:
            raise ValueError("redacted tool identity does not match full outcome")
        if redacted.tool_name != outcome.tool_name or redacted.tool_version != outcome.tool_version:
            raise ValueError("redacted tool identity does not match full outcome")
        if redacted.argument_digest != outcome.arguments_digest:
            raise ValueError("redacted argument digest does not match full outcome")
        if redacted.result.value != outcome.result_status.value:
            raise ValueError("redacted result does not match full outcome")
        if redacted.error_code != outcome.error_code:
            raise ValueError("redacted error does not match full outcome")
        if redacted.duration_ms != outcome.duration_ms:
            raise ValueError("redacted duration does not match full outcome")
        if redacted.occurred_at.astimezone(UTC) != outcome.occurred_at.astimezone(UTC):
            raise ValueError("redacted timestamp does not match full outcome")


__all__ = ["CanonicalGrantsPersistenceV2Adapter"]
