"""Fail-closed adapter boundary for future trusted evidence persistence."""

from __future__ import annotations

from typing import Sequence

from prodavan.application.canonical_grants.contracts import (
    CanonicalAuditEvent,
    CanonicalGrantDecision,
)
from prodavan.application.canonical_grants_promotion.contracts import (
    AuditResult,
    PromotionPartitionKey,
    PromotionRecord,
    QuarantineRecord,
    RedactedAuditOutcome,
)

from .contracts import McpAuditCorrelation
from .ports import TrustedEvidenceRepository


class CanonicalGrantsPersistenceAdapter:
    """Connect promotion/quarantine evidence to a future append-only store.

    This adapter deliberately has no positive canonical-grant write method.
    Evidence marked as enabling positive writes is rejected until a separately
    reviewed transaction boundary exists.
    """

    def __init__(self, repository: TrustedEvidenceRepository) -> None:
        self._repository = repository

    async def read_promotions(
        self, *, partition: PromotionPartitionKey
    ) -> Sequence[PromotionRecord]:
        return await self._repository.read_promotions(partition=partition)

    async def read_quarantine(
        self, *, partition: PromotionPartitionKey
    ) -> Sequence[QuarantineRecord]:
        return await self._repository.read_quarantine(partition=partition)

    async def append_promotion(self, record: PromotionRecord) -> None:
        self._ensure_negative(record.canonical_positive_writes)
        await self._repository.append_evidence(promotion=record)

    async def append_quarantine(self, record: QuarantineRecord) -> None:
        self._ensure_negative(record.canonical_positive_writes)
        await self._repository.append_evidence(quarantine=record)

    async def append_audit_outcome(self, outcome: RedactedAuditOutcome) -> None:
        if not outcome.correlation_id:
            raise ValueError("MCP correlation_id is required for trusted audit persistence")
        await self._repository.append_evidence(audit=outcome)

    async def append_canonical_audit(
        self,
        event: CanonicalAuditEvent,
        *,
        mcp: McpAuditCorrelation,
        profile_id: str,
        tool_server: str,
        tool_name: str,
        tool_version: str,
        argument_digest: str,
        duration_ms: int,
    ) -> None:
        """Map canonical audit decisions to redacted MCP evidence only."""
        if event.decision is CanonicalGrantDecision.ALLOW:
            result = AuditResult.ALLOW
            error_code = None
        elif event.decision is CanonicalGrantDecision.DENY:
            result = AuditResult.DENY
            error_code = event.reason.value
        else:
            result = AuditResult.ERROR
            error_code = event.reason.value
        await self.append_audit_outcome(
            RedactedAuditOutcome(
                mcp_trace_id=mcp.mcp_trace_id,
                span_id=mcp.span_id,
                profile_id=profile_id,
                tool_server=tool_server,
                tool_name=tool_name,
                tool_version=tool_version,
                argument_digest=argument_digest,
                result=result,
                duration_ms=duration_ms,
                occurred_at=event.occurred_at,
                error_code=error_code,
                correlation_id=mcp.correlation_id,
            )
        )

    @staticmethod
    def _ensure_negative(canonical_positive_writes: bool) -> None:
        if canonical_positive_writes:
            raise ValueError("positive canonical writes are disabled in persistence adapter")


__all__ = ["CanonicalGrantsPersistenceAdapter"]
