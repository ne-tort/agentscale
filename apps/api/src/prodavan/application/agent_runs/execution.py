"""Fenced execution port for durable agent runs.

The current host has no implementation for this port. A caller must not claim
that a run executes, retry an ambiguous provider call, or accept a stale lease
until a durable adapter implements every operation below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol

from prodavan.domain.agent.control_plane import AgentRunStatus

from .service import EXECUTION_BLOCKERS


class ProviderOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYABLE_FAILURE = "retryable_failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AgentRunLease:
    tenant_id: str
    project_id: str
    run_id: str
    session_id: str | None
    owner_id: str
    lease_token: str
    generation: int
    attempt: int
    sequence: int
    lease_until: datetime

    def __post_init__(self) -> None:
        for field in ("tenant_id", "project_id", "run_id", "owner_id", "lease_token"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must not be empty")
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if self.generation < 1 or self.attempt < 1 or self.sequence < 1:
            raise ValueError("lease generation, attempt and sequence must be positive")
        if self.lease_until.tzinfo is None or self.lease_until.utcoffset() is None:
            raise ValueError("lease_until must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    outcome: ProviderOutcome
    output_payload: Mapping[str, Any] | None = None
    failure_code: str | None = None
    provider_request_id: str | None = None
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.retry_at is not None and (
            self.retry_at.tzinfo is None or self.retry_at.utcoffset() is None
        ):
            raise ValueError("retry_at must be timezone-aware")
        if self.outcome is ProviderOutcome.UNKNOWN and self.retry_at is not None:
            raise ValueError("unknown provider outcomes must be reconciled, never blind-retried")
        if self.outcome is ProviderOutcome.RETRYABLE_FAILURE and self.retry_at is None:
            raise ValueError("retryable failure requires a durable retry_at")


def validate_retry_result(result: ProviderExecutionResult) -> datetime:
    """Return retry time only for a proven retryable provider failure."""
    if result.outcome is ProviderOutcome.UNKNOWN:
        raise ValueError("unknown provider outcomes must be reconciled, never blind-retried")
    if result.outcome is not ProviderOutcome.RETRYABLE_FAILURE or result.retry_at is None:
        raise ValueError("only a retryable provider failure can be scheduled")
    return result.retry_at


class AgentRunExecutionPort(Protocol):
    """Durable fenced worker operations; each transition commits state + event.

    ``claim_next`` must claim only eligible queued/retryable work using a DB
    lease and monotonically increasing generation. Every mutating method must
    compare the run's generation, lease token, expected sequence and status.
    ``finish_unknown`` persists the ambiguous request and blocks a new provider
    attempt until ``reconcile_unknown`` proves success/failure or an operator
    resolves it. Cancellation increments/fences the generation so late provider
    completions cannot publish output or reopen a cancelled run.
    """

    async def claim_next(
        self,
        *,
        owner_id: str,
        now: datetime,
        lease_until: datetime,
    ) -> AgentRunLease | None: ...

    async def heartbeat(self, *, lease: AgentRunLease, now: datetime, lease_until: datetime) -> bool: ...

    async def finish(
        self,
        *,
        lease: AgentRunLease,
        result: ProviderExecutionResult,
        now: datetime,
    ) -> bool: ...

    async def schedule_retry(
        self,
        *,
        lease: AgentRunLease,
        retry_at: datetime,
        failure_code: str,
        now: datetime,
    ) -> bool: ...

    async def finish_unknown(
        self,
        *,
        lease: AgentRunLease,
        provider_request_id: str | None,
        observed_at: datetime,
    ) -> bool: ...

    async def reconcile_unknown(
        self,
        *,
        tenant_id: str,
        run_id: str,
        generation: int,
        outcome: ProviderExecutionResult,
        now: datetime,
    ) -> bool: ...

    async def cancel_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        command_idempotency_key: str,
        reason: str | None,
    ) -> bool:
        """Atomically cancel, bump/fence generation, append event and receipt."""
        ...


class AgentRunExecutionUnavailable(RuntimeError):
    """No durable, generation-fenced run worker has been wired by the host."""

    def __init__(self) -> None:
        self.blocker = EXECUTION_BLOCKERS["run_claim"]
        super().__init__(
            "Agent Runs are persisted as queue intents only; no durable run claim/lease, "
            "provider outcome journal, or fenced execution consumer is wired"
        )


class AgentRunExecutor:
    """Fail-closed facade; never executes without the durable fencing port."""

    def __init__(self, port: AgentRunExecutionPort | None = None) -> None:
        self._port = port

    @property
    def is_wired(self) -> bool:
        return self._port is not None

    async def claim_next(self, *, owner_id: str, now: datetime, lease_until: datetime) -> AgentRunLease:
        _validate_claim(owner_id=owner_id, now=now, lease_until=lease_until)
        if self._port is None:
            raise AgentRunExecutionUnavailable()
        lease = await self._port.claim_next(owner_id=owner_id, now=now, lease_until=lease_until)
        if lease is None:
            raise LookupError("no eligible Agent Run is available")
        return lease

    async def cancel_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        command_idempotency_key: str,
        reason: str | None = None,
    ) -> bool:
        if self._port is None:
            raise AgentRunExecutionUnavailable()
        if expected_sequence < 1:
            raise ValueError("expected_sequence must be positive")
        if expected_status not in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}:
            raise ValueError("only queued or running runs can be cancelled")
        if not command_idempotency_key.strip():
            raise ValueError("command_idempotency_key must not be empty")
        return await self._port.cancel_run(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            expected_sequence=expected_sequence,
            expected_status=expected_status,
            command_idempotency_key=command_idempotency_key,
            reason=reason,
        )


def _validate_claim(*, owner_id: str, now: datetime, lease_until: datetime) -> None:
    if not owner_id.strip():
        raise ValueError("owner_id must not be empty")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if lease_until.tzinfo is None or lease_until.utcoffset() is None or lease_until <= now:
        raise ValueError("lease_until must be timezone-aware and after now")


__all__ = [
    "AgentRunExecutionPort",
    "AgentRunExecutionUnavailable",
    "AgentRunExecutor",
    "AgentRunLease",
    "ProviderExecutionResult",
    "ProviderOutcome",
]
