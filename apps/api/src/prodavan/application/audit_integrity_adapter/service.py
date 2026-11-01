"""Orchestrate linked audit integrity validation and outcome persistence seams."""
from __future__ import annotations

from prodavan.application.audit_integrity import AuditIntegrityService, AuditOutcomeEvidence
from prodavan.application.audit_outcomes import AuditDecision
from prodavan.application.audit_outcome_adapter import (
    AuditOutcomeEvidence as AdaptedAuditOutcomeEvidence,
    AuditTransactionEvidenceV2,
)

from .contracts import AuditIntegrityAppendRequest
from .ports import AuditOutcomeTransactionPort, DurableQuarantinePort


class AuditIntegrityAdapter:
    """Fail-closed coordinator for validated audit outcome routing.

    Auth and final-result links are validated before either route is touched.
    Quarantine is durable-only; allow/deny use the full outcome transaction seam.
    """

    def __init__(
        self,
        *,
        integrity: type[AuditIntegrityService] = AuditIntegrityService,
        outcome_adapter: AuditOutcomeTransactionPort | None = None,
        quarantine: DurableQuarantinePort | None = None,
    ) -> None:
        if outcome_adapter is None and quarantine is None:
            raise ValueError("an outcome transaction or quarantine port is required")
        self._integrity = integrity
        self._outcome_adapter = outcome_adapter
        self._quarantine = quarantine

    async def append(
        self,
        request: AuditIntegrityAppendRequest,
    ) -> AuditOutcomeEvidence | AdaptedAuditOutcomeEvidence | AuditTransactionEvidenceV2:
        """Validate links first, then route quarantine or trusted evidence."""
        evidence = self._integrity.validate(
            request.outcome,
            auth_event=request.auth_event,
            final_result_event=request.final_result_event,
        )
        if evidence.decision is AuditDecision.QUARANTINE:
            if self._quarantine is None:
                raise RuntimeError("durable quarantine port is required")
            await self._quarantine.append_quarantine(evidence, reason=request.outcome.reason_code)
            return evidence
        if self._outcome_adapter is None:
            raise RuntimeError("outcome transaction port is required")
        return await self._outcome_adapter.append_transaction_evidence(
            request.outcome,
            integrity=evidence,
        )


__all__ = ["AuditIntegrityAdapter"]
