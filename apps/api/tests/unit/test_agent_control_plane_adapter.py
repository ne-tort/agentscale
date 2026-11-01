"""Source tests for the durable agent control-plane application adapter."""

from datetime import UTC, datetime

import pytest

from prodavan.application.agent_control_plane import DurableAgentControlPlane
from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    AgentRunView,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestStatus,
    ApprovalRequestView,
    EventEnvelope,
)
from prodavan.domain.automation.outbox import OutboxEvent


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


class Runs:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def create_or_get(self, command: AgentRunCreate) -> AgentRunView:
        return AgentRunView(
            **command.model_dump(), id="agr-1", status=AgentRunStatus.QUEUED,
            created_at=NOW, updated_at=NOW,
        )

    async def get(self, run_id: str) -> AgentRunView | None:
        return None

    async def append_event(self, envelope: EventEnvelope) -> None:
        self.events.append(envelope)


class Approvals:
    async def create_or_get(self, command: ApprovalRequestCreate) -> ApprovalRequestView:
        return ApprovalRequestView(
            **command.model_dump(), id="apr-1", status=ApprovalRequestStatus.PENDING,
            requested_at=NOW, created_at=NOW, updated_at=NOW,
        )

    async def get(self, approval_id: str) -> ApprovalRequestView | None:
        return None

    async def decide(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRequestView:
        return ApprovalRequestView(
            run_id="agr-1", tool_call_id="tool-1", approval_kind="filesystem.write",
            requested_payload={}, id=approval_id, status=decision.status,
            decision_reason=decision.decision_reason,
            decided_by_employee_id=decision.decided_by_employee_id,
            requested_at=NOW, decided_at=NOW, created_at=NOW, updated_at=NOW,
        )

    async def list_pending(self, run_id: str) -> list[ApprovalRequestView]:
        return []


class Outbox:
    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []
        self.intents: list[object] = []

    async def record_and_enqueue(self, *, event: OutboxEvent, intent: object) -> tuple[OutboxEvent, bool]:
        self.events.append(event)
        self.intents.append(intent)
        return event, True


@pytest.mark.asyncio
async def test_submit_run_emits_ordered_idempotent_outbox_event() -> None:
    runs, outbox = Runs(), Outbox()
    adapter = DurableAgentControlPlane(
        runs=runs, approvals=Approvals(), outbox=outbox, tenant_id="tenant-1", clock=lambda: NOW
    )

    await adapter.submit_run(AgentRunCreate(project_id="project-1", idempotency_key="turn-1"))

    assert runs.events[0].sequence == 1
    assert runs.events[0].idempotency_key.startswith("agent-run:")
    assert runs.events[0].idempotency_key.endswith(":created")
    assert len(runs.events[0].idempotency_key) <= 128
    assert outbox.events[0].aggregate_version == 1
    assert outbox.events[0].idempotency_key == runs.events[0].idempotency_key
    assert outbox.intents[0].deduplication_key == runs.events[0].idempotency_key


@pytest.mark.asyncio
async def test_approval_request_and_decision_have_stable_transition_types() -> None:
    runs, outbox = Runs(), Outbox()
    adapter = DurableAgentControlPlane(
        runs=runs, approvals=Approvals(), outbox=outbox, tenant_id="tenant-1", clock=lambda: NOW
    )

    await adapter.request_approval(
        ApprovalRequestCreate(run_id="agr-1", tool_call_id="tool-1", approval_kind="filesystem.write")
    )
    await adapter.decide_approval(
        "apr-1", ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id="emp-1")
    )

    assert [event.event_type for event in runs.events] == [
        "agent_run.waiting_approval", "approval_request.created", "approval_request.approved"
    ]
    assert [event.sequence for event in runs.events] == [2, 1, 2]
    assert outbox.events[-1].payload["payload"]["status"] == "approved"
