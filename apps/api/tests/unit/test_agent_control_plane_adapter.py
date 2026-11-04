"""Source tests for the durable agent control-plane application adapter.

The adapter no longer composes independent run/approval/outbox repositories:
it builds one ``ControlPlaneAtomicWrite`` per command and hands it to the
repository-owned transaction port (see
``prodavan.application.agent_control_plane.ports``).  These tests therefore
assert what the adapter itself owns -- event ordering, stable bounded
idempotency keys, and the exact projection into outbox events and queue
intents -- through a recording transaction.  The full CAS/conflict/replay
matrix belongs to ``tests/migration/agent_control_plane/test_acceptance_contract.py``.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from prodavan.application.agent_control_plane import (
    AtomicEventOutboxResult,
    ControlPlaneLinkage,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
    DurableAgentControlPlane,
)
from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    AgentRunView,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestStatus,
    ApprovalRequestView,
)
from prodavan.domain.automation.outbox import OutboxEvent

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
LINKAGE = ControlPlaneLinkage(
    tenant_id="tenant-1",
    actor_id="emp-1",
    project_id="project-1",
    session_id="session-1",
    trace_id="trace-1",
)


class AtomicTransaction:
    """Records atomic writes and replays the first result for a repeated command."""

    def __init__(self) -> None:
        self.writes = []
        self.results: dict[tuple[str, str], ControlPlaneWriteResult] = {}

    async def commit(self, *, write):
        self.writes.append(write)
        key = (write.command.linkage.tenant_id, write.command.command_idempotency_key)
        if key in self.results:
            previous = self.results[key]
            return replace(
                previous,
                status=ControlPlaneWriteStatus.DUPLICATE,
                event_results=tuple(replace(item, duplicate=True) for item in previous.event_results),
            )
        event_results = tuple(
            AtomicEventOutboxResult(
                event_id=str(event.event_id),
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.sequence or 0,
                outbox_idempotency_key=outbox_event.idempotency_key,
                queue_deduplication_key=intent.deduplication_key,
                event_recorded=True,
                queue_intent_recorded=True,
            )
            for event, outbox_event, intent in zip(
                write.events, write.outbox_events, write.outbox_intents, strict=True
            )
        )
        approval = None
        if write.command.approval_id is not None:
            approval = ApprovalRequestView(
                run_id=write.command.run_id,
                tool_call_id="tool-1",
                approval_kind="filesystem.write",
                id=write.command.approval_id,
                status=write.command.target_approval_status,
                requested_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        result = ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.APPLIED,
            event_results=event_results,
            run=AgentRunView(
                project_id=write.command.linkage.project_id,
                session_id=write.command.linkage.session_id,
                idempotency_key=write.command.command_idempotency_key,
                id=write.command.run_id,
                status=write.command.target_run_status,
                created_at=NOW,
                updated_at=NOW,
            ),
            approval=approval,
        )
        self.results[key] = result
        return result


def adapter(transaction: AtomicTransaction) -> DurableAgentControlPlane:
    return DurableAgentControlPlane(transaction=transaction, tenant_id="tenant-1", clock=lambda: NOW)


@pytest.mark.asyncio
async def test_submit_run_emits_ordered_idempotent_outbox_event() -> None:
    transaction = AtomicTransaction()
    plane = adapter(transaction)
    command = AgentRunCreate(project_id="project-1", session_id="session-1", idempotency_key="turn-1")

    first = await plane.submit_run(command, linkage=LINKAGE)
    second = await plane.submit_run(command, linkage=LINKAGE)

    assert first.status is ControlPlaneWriteStatus.APPLIED
    write = transaction.writes[0]
    assert [event.sequence for event in write.events] == [1]
    assert [event.event_type for event in write.events] == ["agent_run.created"]
    assert [event.aggregate_type for event in write.events] == ["agent_run"]

    event = write.events[0]
    # The transition key is a digest of (tenant, aggregate, sequence, command
    # key, transition): it must fit the 128-char envelope limit, must not embed
    # the caller's raw idempotency key, and must be identical on a replay.
    assert event.idempotency_key is not None
    assert event.idempotency_key.startswith("agent-transition:")
    assert len(event.idempotency_key) <= 128
    assert "turn-1" not in event.idempotency_key

    outbox_event, intent = write.outbox_events[0], write.outbox_intents[0]
    assert outbox_event.aggregate_version == 1
    assert outbox_event.event_id == str(event.event_id)
    assert outbox_event.idempotency_key == OutboxEvent.aggregate_event_key(
        tenant_id="tenant-1",
        aggregate_type="agent_run",
        aggregate_id=write.command.run_id,
        aggregate_version=1,
    )
    assert intent.deduplication_key == outbox_event.idempotency_key
    assert intent.event_id == outbox_event.event_id

    assert second.status is ControlPlaneWriteStatus.DUPLICATE
    replay = transaction.writes[1]
    assert replay.command.run_id == write.command.run_id
    assert replay.events[0].event_id == event.event_id
    assert replay.events[0].idempotency_key == event.idempotency_key
    assert replay.outbox_events[0].idempotency_key == outbox_event.idempotency_key
    assert replay.outbox_intents[0].deduplication_key == intent.deduplication_key


@pytest.mark.asyncio
async def test_approval_request_and_decision_have_stable_transition_types() -> None:
    transaction = AtomicTransaction()
    plane = adapter(transaction)

    await plane.request_approval(
        ApprovalRequestCreate(run_id="agr-1", tool_call_id="tool-1", approval_kind="filesystem.write"),
        linkage=LINKAGE,
        expected_run_sequence=1,
        expected_run_status=AgentRunStatus.QUEUED,
    )
    approval_id = transaction.writes[0].command.approval_id
    assert approval_id is not None
    await plane.decide_approval(
        approval_id,
        ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id="emp-1"),
        run_id="agr-1",
        linkage=LINKAGE,
        expected_approval_sequence=1,
        expected_run_sequence=2,
    )

    request_write, decision_write = transaction.writes
    assert [event.event_type for event in request_write.events] == [
        "agent_run.waiting_approval",
        "approval_request.created",
    ]
    assert [event.sequence for event in request_write.events] == [2, 1]
    assert [event.aggregate_type for event in decision_write.events] == [
        "approval_request",
        "agent_run",
    ]
    assert [event.event_type for event in decision_write.events] == [
        "approval_request.approved",
        "agent_run.resumed",
    ]
    # The decision advances the approval (1 -> 2) and its linked run (2 -> 3)
    # in one write: a committed approval can never lose its run transition.
    assert [event.sequence for event in decision_write.events] == [2, 3]
    assert decision_write.command.approval_id == approval_id
    assert {
        (aggregate.aggregate_type, aggregate.expected_sequence, aggregate.next_sequence)
        for aggregate in decision_write.command.aggregates
    } == {("approval_request", 1, 2), ("agent_run", 2, 3)}
    assert decision_write.events[0].idempotency_key != request_write.events[1].idempotency_key
    assert len(decision_write.events[0].idempotency_key) <= 128

    outbox_approved = transaction.writes[1].outbox_events[0]
    assert outbox_approved.payload["payload"]["status"] == "approved"
    assert outbox_approved.payload["payload"]["from_status"] == "pending"
    assert outbox_approved.idempotency_key == transaction.writes[1].outbox_intents[0].deduplication_key
