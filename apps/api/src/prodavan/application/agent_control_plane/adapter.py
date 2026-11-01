"""Application adapter for durable agent control-plane transitions.

The adapter deliberately has no HTTP or ORM knowledge. Repositories own durable
state; this layer maps accepted commands to ordered, idempotent domain events
and the automation outbox contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Callable, Literal, cast
from uuid import UUID

from prodavan.application.agent.control_plane_ports import (
    AgentControlPlane,
    AgentRunRepository,
    ApprovalRequestRepository,
)
from prodavan.application.automation_service.outbox import OutboxRepository
from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunView,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestView,
    EventEnvelope,
)
from prodavan.domain.automation.outbox import OutboxEnqueueIntent, OutboxEvent


Clock = Callable[[], datetime]


class DurableAgentControlPlane(AgentControlPlane):
    """Coordinate run/approval repositories with the durable automation outbox.

    Event sequence numbers are scoped to an aggregate. Command idempotency is
    preserved in both the domain envelope and outbox event: a repeated command
    creates no second queue intent when the outbox repository enforces its
    documented uniqueness guarantee.
    """

    def __init__(
        self,
        *,
        runs: AgentRunRepository,
        approvals: ApprovalRequestRepository,
        outbox: OutboxRepository,
        tenant_id: str,
        queue_name: str = "agent-control-plane",
        producer: str = "agent-control-plane",
        clock: Clock | None = None,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if not queue_name.strip():
            raise ValueError("queue_name must not be empty")
        if not producer.strip():
            raise ValueError("producer must not be empty")
        self._runs = runs
        self._approvals = approvals
        self._outbox = outbox
        self._tenant_id = tenant_id
        self._queue_name = queue_name
        self._producer = producer
        self._clock = clock or (lambda: datetime.now(UTC))

    async def submit_run(self, command: AgentRunCreate) -> AgentRunView:
        run = await self._runs.create_or_get(command)
        await self._publish(
            event_type="agent_run.created",
            aggregate_type="agent_run",
            aggregate_id=run.id,
            run_id=run.id,
            session_id=run.session_id,
            sequence=1,
            idempotency_key=_run_idempotency_key(
                project_id=run.project_id, command_key=command.idempotency_key, transition="created"
            ),
            payload={"project_id": run.project_id, "input_payload": run.input_payload},
        )
        return run

    async def request_approval(self, command: ApprovalRequestCreate) -> ApprovalRequestView:
        approval = await self._approvals.create_or_get(command)
        await self._publish(
            event_type="agent_run.waiting_approval",
            aggregate_type="agent_run",
            aggregate_id=command.run_id,
            run_id=command.run_id,
            sequence=2,
            idempotency_key=f"approval:{approval.id}:requested",
            payload={
                "approval_id": approval.id,
                "tool_call_id": approval.tool_call_id,
                "approval_kind": approval.approval_kind,
                "requested_payload": approval.requested_payload,
            },
        )
        await self._publish(
            event_type="approval_request.created",
            aggregate_type="approval_request",
            aggregate_id=approval.id,
            run_id=approval.run_id,
            sequence=1,
            idempotency_key=f"approval:{approval.id}:created",
            payload={"tool_call_id": approval.tool_call_id, "approval_kind": approval.approval_kind},
        )
        return approval

    async def decide_approval(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRequestView:
        approval = await self._approvals.decide(approval_id, decision)
        await self._publish(
            event_type=f"approval_request.{decision.status.value}",
            aggregate_type="approval_request",
            aggregate_id=approval.id,
            run_id=approval.run_id,
            sequence=2,
            idempotency_key=f"approval:{approval.id}:decision:{decision.status.value}",
            payload={
                "status": decision.status.value,
                "decided_by_employee_id": decision.decided_by_employee_id,
                "decision_reason": decision.decision_reason,
            },
        )
        return approval

    async def _publish(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        run_id: str | None = None,
        session_id: str | None = None,
        sequence: int,
        idempotency_key: str,
        payload: dict,
    ) -> None:
        occurred_at = self._clock()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        envelope = EventEnvelope(
            event_type=event_type,
            occurred_at=occurred_at,
            producer=self._producer,
            aggregate_type=cast("Literal['agent_run', 'approval_request', 'agent_session']", aggregate_type),
            aggregate_id=aggregate_id,
            run_id=run_id,
            session_id=session_id,
            correlation_id=_correlation_id(idempotency_key),
            idempotency_key=idempotency_key,
            sequence=sequence,
            payload=payload,
        )
        await self._append(aggregate_type=aggregate_type, envelope=envelope)
        event = OutboxEvent(
            event_id=str(envelope.event_id),
            tenant_id=self._tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=sequence,
            event_type=event_type,
            payload=envelope.model_dump(mode="json"),
            occurred_at=occurred_at,
            idempotency_key=idempotency_key,
        )
        await self._outbox.record_and_enqueue(
            event=event,
            intent=OutboxEnqueueIntent(
                event_id=event.event_id,
                queue_name=self._queue_name,
                deduplication_key=idempotency_key,
            ),
        )

    async def _append(self, *, aggregate_type: str, envelope: EventEnvelope) -> None:
        if aggregate_type == "agent_run":
            await self._runs.append_event(envelope)
        else:
            # Approval repository intentionally has no append_event port yet;
            # the run repository is the durable event stream owner.
            await self._runs.append_event(envelope)


def _run_idempotency_key(*, project_id: str, command_key: str, transition: str) -> str:
    """Keep the stable command identity within the envelope's wire limit."""
    digest = sha256(f"{project_id}\x00{command_key}\x00{transition}".encode("utf-8")).hexdigest()
    return f"agent-run:{digest}:{transition}"


def _correlation_id(key: str) -> UUID:
    """Derive a deterministic UUID so retries retain correlation metadata."""
    return UUID(bytes=sha256(key.encode("utf-8")).digest()[:16])


__all__ = ["DurableAgentControlPlane"]
