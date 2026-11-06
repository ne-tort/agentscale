"""Validate linked authorization/execution evidence before append."""
from __future__ import annotations

from datetime import UTC
from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult

from .contracts import AuditEventClass, AuditLinkedEvent, AuditOutcomeEvidence, IntegrityDisposition
from .ports import DurableQuarantinePort, TrustedAuditAppendPort


class AuditIntegrityService:
    """Fail-closed integrity gate with separate trusted and quarantine routes."""

    def __init__(
        self,
        *,
        trusted: TrustedAuditAppendPort | None = None,
        quarantine: DurableQuarantinePort | None = None,
    ) -> None:
        self._trusted = trusted
        self._quarantine = quarantine

    @classmethod
    def validate(
        cls,
        outcome: AuditOutcome,
        *,
        auth_event: AuditLinkedEvent,
        final_result_event: AuditLinkedEvent,
    ) -> AuditOutcomeEvidence:
        evidence = AuditOutcomeEvidence(
            outcome,
            auth_event.scope_kind,
            auth_event.scope_id,
            auth_event,
            final_result_event,
        )
        cls._validate(evidence)
        return evidence

    async def append(
        self,
        outcome: AuditOutcome,
        *,
        auth_event: AuditLinkedEvent,
        final_result_event: AuditLinkedEvent,
    ) -> AuditOutcomeEvidence:
        evidence = self.validate(outcome, auth_event=auth_event, final_result_event=final_result_event)
        if evidence.quarantine:
            if self._quarantine is None:
                raise RuntimeError("durable quarantine port is required")
            await self._quarantine.append_quarantine(evidence, reason=outcome.reason_code)
            return evidence
        if self._trusted is None:
            raise RuntimeError("trusted audit append port is required")
        await self._trusted.append_trusted(evidence)
        return evidence

    @staticmethod
    def _validate(evidence: AuditOutcomeEvidence) -> None:
        outcome = evidence.outcome
        auth = evidence.auth_event
        final = evidence.final_result_event
        if outcome.decision is AuditDecision.QUARANTINE:
            if outcome.result_status is not AuditResult.ERROR:
                raise ValueError("quarantine must map to error result")
        elif outcome.decision is AuditDecision.ALLOW and outcome.result_status is not AuditResult.ALLOW:
            raise ValueError("allow must map to allow result")
        elif outcome.decision is AuditDecision.DENY and outcome.result_status is AuditResult.ALLOW:
            raise ValueError("deny cannot map to allow result")
        if auth.scope_kind != final.scope_kind or auth.scope_id != final.scope_id:
            raise ValueError("scope_kind/scope_id mismatch between linked events")
        for name in ("correlation_id", "run_id"):
            if getattr(auth, name) != getattr(final, name):
                raise ValueError(f"{name} mismatch between linked events")
        if outcome.correlation_id != auth.correlation_id:
            raise ValueError("outcome correlation_id does not match linked events")
        if outcome.event_id != final.event_id:
            raise ValueError("outcome event_id must identify the final result event")
        if auth.decision is not outcome.decision:
            raise ValueError("auth decision does not match outcome decision")
        if final.decision is not outcome.decision:
            raise ValueError("final result decision does not match outcome decision")
        if final.result_status is not outcome.result_status:
            raise ValueError("final result does not match outcome result")
        if auth.result_status is not outcome.result_status and auth.decision is not AuditDecision.QUARANTINE:
            raise ValueError("auth result does not match execution result")
        if auth.error_code != outcome.error_code or final.error_code != outcome.error_code:
            raise ValueError("error_code mismatch")
        identity = ("tenant_id", "app_id", "app_version_id", "plugin_instance_id", "capability_key")
        for name in identity:
            expected = getattr(outcome, name)
            if auth.identity.get(name) != expected or final.identity.get(name) != expected:
                raise ValueError(f"identity mismatch for {name}")
        digests = ("arguments_digest", "payload_digest", "app_artifact_digest", "capability_snapshot_digest")
        for name in digests:
            expected = getattr(outcome, name)
            if auth.digests.get(name) != expected or final.digests.get(name) != expected:
                raise ValueError(f"digest mismatch for {name}")
        if auth.occurred_at.astimezone(UTC) != final.occurred_at.astimezone(UTC):
            raise ValueError("timestamp mismatch between linked events")
        if outcome.occurred_at.astimezone(UTC) != final.occurred_at.astimezone(UTC):
            raise ValueError("outcome timestamp does not match final result")


__all__ = ["AuditIntegrityService"]
