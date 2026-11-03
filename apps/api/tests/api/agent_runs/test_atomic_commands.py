"""Atomic-command acceptance vectors for durable run and approval decisions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from prodavan.application.agent_control_plane import (
    ControlPlaneLinkage,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
    DurableAgentControlPlane,
)
from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestStatus,
)


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
TENANT = "tenant-agent-run"
PROJECT = "project-agent-run"
ACTOR = "employee-agent-run"
RUN = "agr_acceptance"
APPROVAL = "apr_acceptance"


class ConflictCapture:
    """Capture application writes without pretending to be durable storage."""

    def __init__(self) -> None:
        self.writes = []

    async def commit(self, *, write):
        self.writes.append(write)
        return ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.CONFLICT,
            reason="acceptance_capture_does_not_commit",
        )


def _linkage(*, session_id: str | None = None) -> ControlPlaneLinkage:
    return ControlPlaneLinkage(
        tenant_id=TENANT,
        actor_id=ACTOR,
        project_id=PROJECT,
        session_id=session_id,
        trace_id="trace-agent-run-acceptance",
    )


def test_create_replay_builds_identical_idempotent_event_and_queue_intent() -> None:
    capture = ConflictCapture()
    service = DurableAgentControlPlane(
        transaction=capture,
        tenant_id=TENANT,
        clock=lambda: NOW,
    )
    command = AgentRunCreate(
        project_id=PROJECT,
        requested_by_employee_id=ACTOR,
        idempotency_key="create-1",
        input_payload={"prompt": "hello"},
    )
    linkage = _linkage()
    first = asyncio.run(service.submit_run(command, linkage=linkage, occurred_at=NOW))
    replay = asyncio.run(service.submit_run(command, linkage=linkage, occurred_at=NOW))

    assert first.status is replay.status is ControlPlaneWriteStatus.CONFLICT
    assert len(capture.writes) == 2
    left, right = capture.writes
    assert left.command.command_digest == right.command.command_digest
    assert left.command.run_id == right.command.run_id
    assert left.events[0].event_id == right.events[0].event_id
    assert left.events[0].sequence == 1
    assert left.events[0].aggregate_type == "agent_run"
    assert left.outbox_events[0].idempotency_key == left.outbox_intents[0].deduplication_key
    assert left.command.linkage.tenant_id == TENANT
    assert left.command.linkage.project_id == PROJECT


def test_approve_and_reject_race_commands_share_both_expected_cas_versions() -> None:
    capture = ConflictCapture()
    service = DurableAgentControlPlane(
        transaction=capture,
        tenant_id=TENANT,
        clock=lambda: NOW,
    )

    async def submit_pair():
        return await asyncio.gather(
            service.decide_approval(
                APPROVAL,
                ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id=ACTOR),
                run_id=RUN,
                linkage=_linkage(session_id="session-a"),
                expected_approval_sequence=3,
                expected_run_sequence=9,
                command_idempotency_key="approve-1",
                occurred_at=NOW,
            ),
            service.decide_approval(
                APPROVAL,
                ApprovalDecision(status=ApprovalRequestStatus.DENIED, decided_by_employee_id=ACTOR),
                run_id=RUN,
                linkage=_linkage(session_id="session-a"),
                expected_approval_sequence=3,
                expected_run_sequence=9,
                command_idempotency_key="reject-1",
                occurred_at=NOW,
            ),
        )

    outcomes = asyncio.run(submit_pair())
    assert all(result.status is ControlPlaneWriteStatus.CONFLICT for result in outcomes)
    assert len(capture.writes) == 2
    for write in capture.writes:
        versions = {
            (version.aggregate_type, version.aggregate_id): version.expected_sequence
            for version in write.command.aggregates
        }
        assert versions[("approval_request", APPROVAL)] == 3
        assert versions[("agent_run", RUN)] == 9
        assert write.command.expected_approval_status is ApprovalRequestStatus.PENDING
        assert write.command.expected_run_status is AgentRunStatus.WAITING_APPROVAL
        assert [(event.aggregate_type, event.sequence) for event in write.events] == [
            ("approval_request", 2),
            ("agent_run", 5),
        ]
        assert len(write.outbox_intents) == 2
        assert all(intent.event_id == event.event_id for intent, event in zip(write.outbox_intents, write.events))

    targets = {write.command.target_approval_status for write in capture.writes}
    assert targets == {ApprovalRequestStatus.APPROVED, ApprovalRequestStatus.DENIED}


def test_run_cancel_command_is_a_status_and_sequence_cas_with_stable_key() -> None:
    capture = ConflictCapture()
    service = DurableAgentControlPlane(
        transaction=capture,
        tenant_id=TENANT,
        clock=lambda: NOW,
    )
    result = asyncio.run(
        service.cancel_run(
            run_id=RUN,
            linkage=_linkage(),
            expected_sequence=6,
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-1",
            reason="operator requested",
            occurred_at=NOW,
        )
    )
    assert result.status is ControlPlaneWriteStatus.CONFLICT
    write = capture.writes[0]
    assert write.command.operation == "agent_run.cancel"
    assert write.command.command_idempotency_key == "cancel-1"
    assert write.command.aggregates[0].expected_sequence == 6
    assert write.command.expected_run_status is AgentRunStatus.RUNNING
    assert write.command.target_run_status is AgentRunStatus.CANCELLED
    assert write.events[0].sequence == 7
    assert write.events[0].event_type == "agent_run.cancelled"


def test_execution_port_specifies_generation_fenced_cancel_and_unknown_reconciliation() -> None:
    from typing import get_args, get_type_hints

    from prodavan.application.agent_runs.execution import AgentRunExecutionPort, AgentRunLease

    lease_type = get_type_hints(AgentRunExecutionPort.claim_next)["return"]
    assert AgentRunLease in get_args(lease_type)
