"""Decision-only integration of audit outcomes with canonical evidence ports."""

from __future__ import annotations

from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult
from prodavan.application.canonical_grants_promotion import (
    AuditResult as PersistenceAuditResult,
    RedactedAuditOutcome,
)

from prodavan.application.audit_integrity import AuditOutcomeEvidence as LinkedAuditOutcomeEvidence

from .contracts import AuditOutcomeEvidence, AuditTransactionEvidenceV2
from .ports import CanonicalGrantsEvidencePort, AuditOutcomeTransactionEvidencePort


_REQUIRED_IDENTITY = (
    "tenant_id",
    "app_definition_id",
    "app_id",
    "app_version_id",
    "manifest_version",
    "app_artifact_digest",
    "plugin_instance_id",
    "runtime_generation",
    "writer_generation",
    "lease_id",
    "capability_grant_id",
    "capability_key",
    "source_kind",
    "source_id",
    "mapping_version",
    "capability_mapping_digest",
    "capability_snapshot_digest",
    "arguments_digest",
    "payload_digest",
)


class AuditOutcomeAdapter:
    """Validate complete evidence and map it to one append-only transaction item."""

    def __init__(
        self,
        *,
        evidence: CanonicalGrantsEvidencePort | None = None,
        transaction: AuditOutcomeTransactionEvidencePort | None = None,
    ) -> None:
        if evidence is None and transaction is None:
            raise ValueError("an evidence or transaction port is required")
        self._evidence = evidence
        self._transaction = transaction

    @classmethod
    def adapt(cls, outcome: AuditOutcome) -> AuditOutcomeEvidence:
        cls._validate_complete(outcome)
        return AuditOutcomeEvidence(outcome=outcome, redacted=cls._redacted(outcome))

    async def append(self, outcome: AuditOutcome) -> AuditOutcomeEvidence:
        """Append redacted evidence without changing the decision or execution result."""
        evidence = self.adapt(outcome)
        if self._evidence is None:
            raise RuntimeError("canonical evidence port is required")
        await self._evidence.append_evidence(audit=evidence.redacted)
        return evidence

    async def append_transaction_evidence(
        self,
        outcome: AuditOutcome,
        *,
        integrity: LinkedAuditOutcomeEvidence | None = None,
    ) -> AuditOutcomeEvidence | AuditTransactionEvidenceV2:
        """Append legacy evidence or a versioned linked transaction envelope."""
        evidence = self.adapt(outcome)
        if self._transaction is None:
            raise RuntimeError("transaction evidence port is required")
        if integrity is None:
            await self._transaction.append_transaction_evidence(evidence)
            return evidence
        transaction_evidence = AuditTransactionEvidenceV2(legacy=evidence, integrity=integrity)
        await self._transaction.append_transaction_evidence(transaction_evidence)
        return transaction_evidence

    @staticmethod
    def _validate_complete(outcome: AuditOutcome) -> None:
        for name in _REQUIRED_IDENTITY:
            value = getattr(outcome, name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"complete {name} is required")
        if not outcome.correlation_id.strip():
            raise ValueError("correlation_id is required")
        if outcome.decision is AuditDecision.QUARANTINE:
            if outcome.result_status is not AuditResult.ERROR:
                raise ValueError("quarantine decision requires error result")
            raise ValueError("quarantine outcome cannot enter trusted evidence")
        if outcome.decision is AuditDecision.ALLOW and outcome.result_status is not AuditResult.ALLOW:
            raise ValueError("allow decision/result mismatch")
        if outcome.decision is AuditDecision.DENY and outcome.result_status is AuditResult.ALLOW:
            raise ValueError("deny decision/result mismatch")

    @staticmethod
    def _redacted(outcome: AuditOutcome) -> RedactedAuditOutcome:
        result = PersistenceAuditResult(outcome.result_status.value)
        return RedactedAuditOutcome(
            mcp_trace_id=outcome.trace_id,
            span_id=outcome.span_id,
            profile_id=outcome.profile_id,
            tool_server=outcome.tool_server,
            tool_name=outcome.tool_name,
            tool_version=outcome.tool_version,
            argument_digest=outcome.arguments_digest,
            result=result,
            duration_ms=outcome.duration_ms,
            occurred_at=outcome.occurred_at,
            error_code=outcome.error_code,
            correlation_id=outcome.correlation_id,
        )


__all__ = ["AuditOutcomeAdapter"]
