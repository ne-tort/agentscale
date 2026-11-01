"""Decision and append orchestration for redacted MCP audit outcomes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .contracts import AuditDecision, AuditOutcome, AuditResult
from .ports import AuditOutcomeAppendPort, AuditOutcomeTransactionPort


class AuditOutcomeService:
    """Build and optionally append immutable audit outcomes without raw input access."""

    def __init__(
        self,
        *,
        append: AuditOutcomeAppendPort | None = None,
        transaction: AuditOutcomeTransactionPort | None = None,
    ) -> None:
        self._append = append
        self._transaction = transaction

    @staticmethod
    def map_result(result: AuditDecision | str) -> AuditDecision:
        """Normalize the three externally visible outcomes and fail closed."""
        try:
            return result if isinstance(result, AuditDecision) else AuditDecision(result)
        except (TypeError, ValueError) as exc:
            raise ValueError("result must map to allow, deny, or quarantine") from exc

    def enforce_correlation(
        self,
        outcome: AuditOutcome,
        *,
        correlation_id: str,
    ) -> AuditOutcome:
        """Require one correlation id across request context and MCP envelope."""
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise ValueError("correlation_id must not be empty")
        if outcome.correlation_id != correlation_id:
            raise ValueError("correlation_id does not match audit envelope")
        return outcome

    async def record(
        self,
        outcome: AuditOutcome,
        *,
        correlation_id: str,
        transaction_evidence: bool = False,
    ) -> AuditOutcome:
        """Validate correlation and append evidence through the supplied port only."""
        checked = self.enforce_correlation(outcome, correlation_id=correlation_id)
        if transaction_evidence:
            if self._transaction is None:
                raise RuntimeError("transaction evidence port is required")
            await self._transaction.append_transaction_evidence(checked)
        elif self._append is not None:
            await self._append.append(checked)
        return checked

    @staticmethod
    def outcome(
        template: AuditOutcome,
        *,
        result: AuditDecision | str,
        error_code: str | None = None,
        result_status: AuditResult | str | None = None,
        duration_ms: int | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditOutcome:
        """Map a decision while preserving the redacted envelope and its digests."""
        decision = AuditOutcomeService.map_result(result)
        if result_status is None:
            execution = AuditResult.ALLOW if decision is AuditDecision.ALLOW else (
                AuditResult.DENY if decision is AuditDecision.DENY else AuditResult.ERROR
            )
        else:
            try:
                execution = result_status if isinstance(result_status, AuditResult) else AuditResult(result_status)
            except (TypeError, ValueError) as exc:
                raise ValueError("result_status must map to allow, deny, or error") from exc
        return replace(
            template,
            decision=decision,
            result_status=execution,
            error_code=error_code,
            duration_ms=template.duration_ms if duration_ms is None else duration_ms,
            occurred_at=template.occurred_at if occurred_at is None else occurred_at,
        )


__all__ = ["AuditOutcomeService"]
