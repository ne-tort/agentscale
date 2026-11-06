"""Behavioral CAS/replay vectors over a small in-test transaction double."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

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
    ApprovalRequestStatus,
    ApprovalRequestView,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
TENANT = "tenant-cas"
PROJECT = "project-cas"
ACTOR = "employee-cas"
RUN_ID = "agr_cas"
APPROVAL_ID = "apr_cas"


class CasTransaction:
    """Minimal deterministic receipt/CAS adapter for acceptance behavior only."""

    def __init__(self) -> None:
        self.sequences: dict[tuple[str, str], int] = {}
        self.statuses: dict[tuple[str, str], object] = {}
        self.receipts: dict[tuple[str, str], tuple[str, ControlPlaneWriteResult]] = {}
        self.writes = []
        self.committed = []

    def seed(self, aggregate_type: str, aggregate_id: str, sequence: int, status) -> None:
        identity = (aggregate_type, aggregate_id)
        self.sequences[identity] = sequence
        self.statuses[identity] = status

    async def commit(self, *, write) -> ControlPlaneWriteResult:
        self.writes.append(write)
        command = write.command
        receipt_key = (command.operation, command.command_idempotency_key)
        previous = self.receipts.get(receipt_key)
        if previous is not None:
            digest, result = previous
            if digest != command.command_digest:
                return ControlPlaneWriteResult(
                    status=ControlPlaneWriteStatus.CONFLICT,
                    reason="idempotency_key_payload_conflict",
                )
            return replace(
                result,
                status=ControlPlaneWriteStatus.DUPLICATE,
                event_results=tuple(replace(item, duplicate=True) for item in result.event_results),
            )

        for version in command.aggregates:
            identity = (version.aggregate_type, version.aggregate_id)
            if self.sequences.get(identity, 0) != version.expected_sequence:
                return ControlPlaneWriteResult(
                    status=ControlPlaneWriteStatus.CONFLICT,
                    reason="aggregate_sequence_conflict",
                )
            expected_status = (
                command.expected_run_status
                if version.aggregate_type == "agent_run"
                else command.expected_approval_status
            )
            if expected_status is not None and self.statuses.get(identity) != expected_status:
                return ControlPlaneWriteResult(
                    status=ControlPlaneWriteStatus.CONFLICT,
                    reason="aggregate_status_conflict",
                )

        event_results = tuple(
            AtomicEventOutboxResult(
                event_id=str(event.event_id),
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.sequence,
                outbox_idempotency_key=outbox.idempotency_key,
                queue_deduplication_key=intent.deduplication_key,
                event_recorded=True,
                queue_intent_recorded=True,
            )
            for event, outbox, intent in zip(write.events, write.outbox_events, write.outbox_intents)
        )
        run = AgentRunView(
            id=command.run_id,
            project_id=command.linkage.project_id,
            session_id=command.linkage.session_id,
            requested_by_employee_id=command.linkage.actor_id,
            idempotency_key=command.command_idempotency_key,
            input_payload=dict(command.details.get("input_payload", {})),
            status=command.target_run_status,
            finished_at=NOW if command.target_run_status in {
                AgentRunStatus.SUCCEEDED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
            } else None,
            created_at=NOW,
            updated_at=NOW,
        )
        approval = None
        if command.approval_id is not None:
            approval = ApprovalRequestView(
                id=command.approval_id,
                run_id=command.run_id,
                tool_call_id=str(command.details.get("tool_call_id", "tool-call")),
                approval_kind=str(command.details.get("approval_kind", "filesystem.write")),
                requested_payload=dict(command.details.get("requested_payload", {})),
                status=command.target_approval_status,
                decision_reason=command.details.get("reason"),
                decided_by_employee_id=command.linkage.actor_id,
                requested_at=NOW,
                expires_at=None,
                decided_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        result = ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.APPLIED,
            event_results=event_results,
            run=run,
            approval=approval,
        )
        for version in command.aggregates:
            identity = (version.aggregate_type, version.aggregate_id)
            self.sequences[identity] = version.next_sequence
            self.statuses[identity] = (
                command.target_run_status
                if version.aggregate_type == "agent_run"
                else command.target_approval_status
            )
        self.receipts[receipt_key] = (command.command_digest, result)
        self.committed.append(write)
        return result


def _service(transaction: CasTransaction) -> DurableAgentControlPlane:
    return DurableAgentControlPlane(transaction=transaction, tenant_id=TENANT, clock=lambda: NOW)


def _linkage() -> ControlPlaneLinkage:
    return ControlPlaneLinkage(TENANT, ACTOR, PROJECT, "session-cas", "trace-cas")


def test_one_competing_approval_decision_wins_and_stale_peer_conflicts() -> None:
    transaction = CasTransaction()
    transaction.seed("approval_request", APPROVAL_ID, 1, ApprovalRequestStatus.PENDING)
    transaction.seed("agent_run", RUN_ID, 4, AgentRunStatus.WAITING_APPROVAL)
    service = _service(transaction)

    async def decide_both():
        return await asyncio.gather(
            service.decide_approval(
                APPROVAL_ID,
                ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id=ACTOR),
                run_id=RUN_ID,
                linkage=_linkage(),
                expected_approval_sequence=1,
                expected_run_sequence=4,
                command_idempotency_key="approve-key",
            ),
            service.decide_approval(
                APPROVAL_ID,
                ApprovalDecision(status=ApprovalRequestStatus.DENIED, decided_by_employee_id=ACTOR),
                run_id=RUN_ID,
                linkage=_linkage(),
                expected_approval_sequence=1,
                expected_run_sequence=4,
                command_idempotency_key="deny-key",
            ),
        )

    first, second = asyncio.run(decide_both())
    assert {first.status, second.status} == {
        ControlPlaneWriteStatus.APPLIED,
        ControlPlaneWriteStatus.CONFLICT,
    }
    assert len(transaction.committed) == 1
    assert transaction.sequences[("approval_request", APPROVAL_ID)] == 2
    assert transaction.sequences[("agent_run", RUN_ID)] == 5
    assert transaction.statuses[("agent_run", RUN_ID)] in {AgentRunStatus.RUNNING, AgentRunStatus.FAILED}
    assert len(transaction.committed[0].events) == 2
    assert len(transaction.committed[0].outbox_intents) == 2


def test_cancel_replay_returns_original_command_result_and_does_not_advance_sequence() -> None:
    transaction = CasTransaction()
    transaction.seed("agent_run", RUN_ID, 6, AgentRunStatus.RUNNING)
    service = _service(transaction)

    async def cancel_twice():
        first = await service.cancel_run(
            run_id=RUN_ID,
            linkage=_linkage(),
            expected_sequence=6,
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-key",
            reason="operator cancelled",
        )
        replay = await service.cancel_run(
            run_id=RUN_ID,
            linkage=_linkage(),
            expected_sequence=6,
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-key",
            reason="operator cancelled",
        )
        return first, replay

    first, replay = asyncio.run(cancel_twice())
    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert replay.status is ControlPlaneWriteStatus.DUPLICATE
    assert replay.run == first.run
    assert transaction.sequences[("agent_run", RUN_ID)] == 7
    assert len(transaction.committed) == 1


def test_run_create_replay_does_not_create_a_second_atomic_write() -> None:
    transaction = CasTransaction()
    service = _service(transaction)
    command = AgentRunCreate(
        project_id=PROJECT,
        session_id="session-cas",
        requested_by_employee_id=ACTOR,
        idempotency_key="create-key",
        input_payload={"prompt": "run"},
    )

    async def create_twice():
        first = await service.submit_run(command, linkage=_linkage(), occurred_at=NOW)
        replay = await service.submit_run(command, linkage=_linkage(), occurred_at=NOW)
        return first, replay

    first, replay = asyncio.run(create_twice())
    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert replay.status is ControlPlaneWriteStatus.DUPLICATE
    assert replay.run == first.run
    assert transaction.sequences[("agent_run", first.run.id)] == 1
    assert len(transaction.committed) == 1


def test_stale_lease_generation_must_be_present_on_every_completion_operation() -> None:
    from typing import get_args, get_type_hints

    from prodavan.application.agent_runs.execution import AgentRunExecutionPort, AgentRunLease

    lease_type = get_type_hints(AgentRunExecutionPort.finish)["lease"]
    assert AgentRunLease in get_args(lease_type) or lease_type is AgentRunLease
    for method_name in ("heartbeat", "finish", "finish_unknown", "schedule_retry"):
        annotations = get_type_hints(AgentRunExecutionPort.__dict__[method_name])
        assert annotations["lease"] is AgentRunLease
    cancel_annotations = get_type_hints(AgentRunExecutionPort.cancel_run)
    assert cancel_annotations["expected_sequence"] is int
    assert cancel_annotations["command_idempotency_key"] is str
