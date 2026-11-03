"""MIG-AUTO-03 acceptance contract tests; deliberately not executed."""

from dataclasses import replace
from datetime import datetime

import pytest

from prodavan.application.agent_control_plane import (
    AtomicEventOutboxResult,
    ControlPlaneResultError,
    ControlPlaneWriteError,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
    DurableAgentControlPlane,
    WIRING_BLOCKER,
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

from .acceptance_fixtures import APPROVAL_EXPIRY, CASES, LINKAGE, NOW


class RecordingTransaction:
    def __init__(self) -> None:
        self.writes = []
        self.fail_with: BaseException | None = None
        self.aggregate_sequences = {}
        self.aggregate_statuses = {}
        self.command_digests = {}
        self.command_results = {}
        self.committed_writes = []
        self.invalid_result = False

    def seed(self, *, aggregate_type: str, aggregate_id: str, sequence: int, status) -> None:
        identity = ("tenant-1", aggregate_type, aggregate_id)
        self.aggregate_sequences[identity] = sequence
        self.aggregate_statuses[identity] = status

    @staticmethod
    def _conflict(reason: str) -> ControlPlaneWriteResult:
        return ControlPlaneWriteResult(status=ControlPlaneWriteStatus.CONFLICT, reason=reason)

    async def commit(self, *, write):
        if self.fail_with is not None:
            raise self.fail_with
        self.writes.append(write)
        command_key = (write.command.linkage.tenant_id, write.command.command_idempotency_key)
        digest = write.command.command_digest
        if command_key in self.command_digests:
            if self.command_digests[command_key] != digest:
                return self._conflict("idempotency_key_payload_conflict")
            previous = self.command_results[command_key]
            return replace(
                previous,
                status=ControlPlaneWriteStatus.DUPLICATE,
                event_results=tuple(replace(item, duplicate=True) for item in previous.event_results),
            )

        for aggregate in write.command.aggregates:
            identity = (write.command.linkage.tenant_id, aggregate.aggregate_type, aggregate.aggregate_id)
            current = self.aggregate_sequences.get(identity, 0)
            if current != aggregate.expected_sequence:
                return self._conflict("aggregate_sequence_conflict")
        run_identity = (write.command.linkage.tenant_id, "agent_run", write.command.run_id)
        current_run_status = self.aggregate_statuses.get(run_identity)
        if write.command.expected_run_status is not None and current_run_status != write.command.expected_run_status:
            return self._conflict("run_status_conflict")
        if write.command.approval_id is not None and write.command.expected_approval_status is not None:
            approval_identity = (
                write.command.linkage.tenant_id,
                "approval_request",
                write.command.approval_id,
            )
            if self.aggregate_statuses.get(approval_identity) != write.command.expected_approval_status:
                return self._conflict("approval_status_conflict")

        results = tuple(
            AtomicEventOutboxResult(
                event_id=str(event.event_id),
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.sequence or 0,
                outbox_idempotency_key=write.outbox_events[index].idempotency_key,
                queue_deduplication_key=write.outbox_intents[index].deduplication_key,
                event_recorded=True,
                queue_intent_recorded=True,
                duplicate=False,
            )
            for index, event in enumerate(write.events)
        )
        details = write.command.details
        run = AgentRunView(
            project_id=write.command.linkage.project_id,
            session_id=write.command.linkage.session_id,
            requested_by_employee_id=details.get("requested_by_employee_id"),
            idempotency_key=write.command.command_idempotency_key,
            input_payload=details.get("input_payload", {}),
            id=write.command.run_id,
            status=write.command.target_run_status,
            finished_at=NOW if write.command.target_run_status in {
                AgentRunStatus.SUCCEEDED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
            } else None,
            created_at=NOW,
            updated_at=NOW,
        )
        approval = None
        if write.command.approval_id is not None:
            detail = details
            approval = ApprovalRequestView(
                run_id=write.command.run_id,
                tool_call_id=detail.get("tool_call_id", "tool-1"),
                approval_kind=detail.get("approval_kind", "filesystem.write"),
                requested_payload=detail.get("requested_payload", {}),
                expires_at=(
                    datetime.fromisoformat(detail["expires_at"])
                    if detail.get("expires_at")
                    else None
                ),
                id=write.command.approval_id,
                status=write.command.target_approval_status,
                requested_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        result = ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.APPLIED,
            event_results=results,
            run=run,
            approval=approval,
        )
        if self.invalid_result:
            result = replace(
                result,
                event_results=(
                    replace(
                        results[0],
                        outbox_idempotency_key="wrong-queue-key",
                        queue_deduplication_key="wrong-queue-key",
                    ),
                    *results[1:],
                ),
            )
        for aggregate in write.command.aggregates:
            identity = (write.command.linkage.tenant_id, aggregate.aggregate_type, aggregate.aggregate_id)
            self.aggregate_sequences[identity] = aggregate.next_sequence
        self.aggregate_statuses[run_identity] = write.command.target_run_status
        if write.command.approval_id is not None:
            approval_identity = (write.command.linkage.tenant_id, "approval_request", write.command.approval_id)
            self.aggregate_statuses[approval_identity] = write.command.target_approval_status
        self.command_digests[command_key] = digest
        self.command_results[command_key] = result
        self.committed_writes.append(write)
        return result


@pytest.fixture
def transaction() -> RecordingTransaction:
    return RecordingTransaction()


@pytest.fixture
def service(transaction: RecordingTransaction) -> DurableAgentControlPlane:
    return DurableAgentControlPlane(transaction=transaction, tenant_id="tenant-1", clock=lambda: NOW)


def test_acceptance_matrix_is_explicit_and_unexecuted() -> None:
    assert len(CASES) >= 13
    assert LINKAGE.session_id == "session-1"
    assert APPROVAL_EXPIRY > NOW


@pytest.mark.asyncio
async def test_submit_run_builds_one_atomic_event_outbox_and_queue_write(service, transaction) -> None:
    result = await service.submit_run(
        AgentRunCreate(
            project_id="project-1",
            session_id="session-1",
            requested_by_employee_id="employee-1",
            idempotency_key="command-1",
            input_payload={"prompt": "run"},
        ),
        linkage=LINKAGE,
    )

    assert result.status is ControlPlaneWriteStatus.APPLIED
    assert len(transaction.writes) == 1
    write = transaction.writes[0]
    assert write.command.aggregates[0].expected_sequence == 0
    assert write.command.aggregates[0].next_sequence == 1
    assert write.events[0].sequence == 1
    assert write.events[0].run_id == write.command.run_id
    assert write.events[0].payload["control_plane"]["trace_id"] == "trace-1"
    assert write.outbox_events[0].event_id == str(write.events[0].event_id)
    assert write.outbox_events[0].idempotency_key == OutboxEvent.aggregate_event_key(
        tenant_id="tenant-1",
        aggregate_type="agent_run",
        aggregate_id=write.command.run_id,
        aggregate_version=1,
    )
    assert write.outbox_intents[0].deduplication_key == write.outbox_events[0].idempotency_key


@pytest.mark.asyncio
async def test_same_command_replay_is_duplicate_without_new_queue_effect(service, transaction) -> None:
    command = AgentRunCreate(project_id="project-1", idempotency_key="command-replay")
    linkage = replace(LINKAGE, session_id=None)
    first = await service.submit_run(command, linkage=linkage)
    second = await service.submit_run(command, linkage=linkage)

    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert second.status is ControlPlaneWriteStatus.DUPLICATE
    assert transaction.writes[0].command.command_digest == transaction.writes[1].command.command_digest
    assert transaction.writes[0].events[0].event_id == transaction.writes[1].events[0].event_id
    assert transaction.writes[0].outbox_intents[0].deduplication_key == transaction.writes[1].outbox_intents[0].deduplication_key
    assert len(transaction.committed_writes) == 1


@pytest.mark.asyncio
async def test_run_transition_carries_cas_sequence_and_timeout(service, transaction) -> None:
    transaction.seed(
        aggregate_type="agent_run",
        aggregate_id="agr_1",
        sequence=4,
        status=AgentRunStatus.RUNNING,
    )
    await service.timeout_run(
        run_id="agr_1",
        linkage=LINKAGE,
        expected_sequence=4,
        expected_status=AgentRunStatus.RUNNING,
        command_idempotency_key="timeout-command",
    )

    write = transaction.writes[0]
    assert write.command.aggregates[0].expected_sequence == 4
    assert write.events[0].sequence == 5
    assert write.events[0].event_type == "agent_run.timed_out"
    assert write.events[0].payload["status"] == AgentRunStatus.FAILED.value
    assert write.events[0].payload["failure_code"] == "agent_run.timeout"


@pytest.mark.asyncio
async def test_cancel_run_is_a_terminal_transition_with_reason(service, transaction) -> None:
    transaction.seed(
        aggregate_type="agent_run",
        aggregate_id="agr_1",
        sequence=2,
        status=AgentRunStatus.RUNNING,
    )
    await service.cancel_run(
        run_id="agr_1",
        linkage=LINKAGE,
        expected_sequence=2,
        expected_status=AgentRunStatus.RUNNING,
        command_idempotency_key="cancel-run-1",
        reason="operator requested",
    )
    event = transaction.writes[0].events[0]
    assert event.event_type == "agent_run.cancelled"
    assert event.sequence == 3
    assert event.payload["status"] == AgentRunStatus.CANCELLED.value
    assert event.payload["reason"] == "operator requested"


@pytest.mark.asyncio
async def test_stale_aggregate_sequence_cannot_commit_second_transition(service, transaction) -> None:
    transaction.seed(
        aggregate_type="agent_run",
        aggregate_id="agr_1",
        sequence=0,
        status=AgentRunStatus.QUEUED,
    )
    first = await service.start_run(
        run_id="agr_1",
        linkage=LINKAGE,
        expected_sequence=0,
        command_idempotency_key="start-one",
    )
    stale = await service.start_run(
        run_id="agr_1",
        linkage=LINKAGE,
        expected_sequence=0,
        command_idempotency_key="start-two",
    )

    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert stale.status is ControlPlaneWriteStatus.CONFLICT
    assert stale.reason == "aggregate_sequence_conflict"
    assert len(transaction.committed_writes) == 1
    assert transaction.aggregate_sequences[("tenant-1", "agent_run", "agr_1")] == 1


@pytest.mark.asyncio
async def test_approval_request_has_two_aggregate_sequences_in_one_write(service, transaction) -> None:
    transaction.seed(
        aggregate_type="agent_run",
        aggregate_id="agr_1",
        sequence=3,
        status=AgentRunStatus.RUNNING,
    )
    await service.request_approval(
        ApprovalRequestCreate(
            run_id="agr_1",
            tool_call_id="tool-1",
            approval_kind="filesystem.write",
            requested_payload={"path": "/workspace/a"},
            expires_at=APPROVAL_EXPIRY,
        ),
        linkage=LINKAGE,
        expected_run_sequence=3,
        expected_run_status=AgentRunStatus.RUNNING,
    )

    write = transaction.writes[0]
    assert {(item.aggregate_type, item.expected_sequence, item.next_sequence) for item in write.command.aggregates} == {
        ("agent_run", 3, 4),
        ("approval_request", 0, 1),
    }
    assert [event.event_type for event in write.events] == [
        "agent_run.waiting_approval",
        "approval_request.created",
    ]
    assert [event.sequence for event in write.events] == [4, 1]
    assert write.events[1].payload["control_plane"]["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_approval_decision_cancel_and_expiry_are_linked_transitions(service, transaction) -> None:
    approval_id = "apr_1"
    transaction.seed(
        aggregate_type="approval_request",
        aggregate_id=approval_id,
        sequence=1,
        status=ApprovalRequestStatus.PENDING,
    )
    transaction.seed(
        aggregate_type="agent_run",
        aggregate_id="agr_1",
        sequence=4,
        status=AgentRunStatus.WAITING_APPROVAL,
    )
    await service.decide_approval(
        approval_id,
        ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id="employee-1"),
        run_id="agr_1",
        linkage=LINKAGE,
        expected_approval_sequence=1,
        expected_run_sequence=4,
    )
    assert [event.event_type for event in transaction.writes[0].events] == [
        "approval_request.approved",
        "agent_run.resumed",
    ]
    assert transaction.writes[0].events[0].sequence == 2
    assert transaction.writes[0].events[1].sequence == 5

    expiry_transaction = RecordingTransaction()
    expiry_transaction.seed(
        aggregate_type="approval_request",
        aggregate_id=approval_id,
        sequence=1,
        status=ApprovalRequestStatus.PENDING,
    )
    expiry_transaction.seed(
        aggregate_type="agent_run",
        aggregate_id="agr_1",
        sequence=4,
        status=AgentRunStatus.WAITING_APPROVAL,
    )
    expiry_service = DurableAgentControlPlane(transaction=expiry_transaction, tenant_id="tenant-1", clock=lambda: NOW)
    await expiry_service.expire_approval(
        approval_id=approval_id,
        run_id="agr_1",
        linkage=LINKAGE,
        expected_approval_sequence=1,
        expected_run_sequence=4,
        expires_at=NOW,
    )
    assert [event.event_type for event in expiry_transaction.writes[0].events] == [
        "approval_request.expired",
        "agent_run.timed_out",
    ]
    assert expiry_transaction.writes[0].events[1].payload["failure_code"] == "approval.expired"


@pytest.mark.asyncio
async def test_cancel_approval_atomically_cancels_linked_run(service, transaction) -> None:
    transaction.seed(
        aggregate_type="approval_request",
        aggregate_id="apr_1",
        sequence=1,
        status=ApprovalRequestStatus.PENDING,
    )
    transaction.seed(
        aggregate_type="agent_run",
        aggregate_id="agr_1",
        sequence=4,
        status=AgentRunStatus.WAITING_APPROVAL,
    )

    await service.cancel_approval(
        approval_id="apr_1",
        run_id="agr_1",
        linkage=LINKAGE,
        expected_approval_sequence=1,
        expected_run_sequence=4,
        reason="operator cancelled run",
    )

    assert [event.event_type for event in transaction.writes[0].events] == [
        "approval_request.cancelled",
        "agent_run.cancelled",
    ]
    assert [item.next_sequence for item in transaction.writes[0].command.aggregates] == [2, 5]


@pytest.mark.asyncio
async def test_write_error_is_wrapped_and_does_not_fallback(service, transaction) -> None:
    transaction.fail_with = OSError("database unavailable")

    with pytest.raises(ControlPlaneWriteError) as error:
        await service.submit_run(AgentRunCreate(project_id="project-1", idempotency_key="write-error"), linkage=replace(LINKAGE, session_id=None))

    assert error.value.operation == "agent_run.create"
    assert error.value.command_idempotency_key == "write-error"
    assert len(transaction.writes) == 0


@pytest.mark.asyncio
async def test_missing_transaction_wiring_is_blocked_without_side_effects() -> None:
    service = DurableAgentControlPlane(transaction=None, tenant_id="tenant-1", clock=lambda: NOW)

    result = await service.submit_run(
        AgentRunCreate(project_id="project-1", idempotency_key="blocked"),
        linkage=replace(LINKAGE, session_id=None),
    )

    assert result.status is ControlPlaneWriteStatus.BLOCKED
    assert result.blocker == WIRING_BLOCKER
    assert service.wiring_blocker == WIRING_BLOCKER


@pytest.mark.asyncio
async def test_linkage_mismatch_is_rejected_before_transaction_call(service, transaction) -> None:
    with pytest.raises(ValueError, match="project_id"):
        await service.submit_run(
            AgentRunCreate(project_id="other-project", idempotency_key="wrong-project"),
            linkage=replace(LINKAGE, session_id=None),
        )
    assert transaction.writes == []


@pytest.mark.asyncio
async def test_duplicate_result_requires_all_event_results_to_be_duplicate(transaction) -> None:
    service = DurableAgentControlPlane(transaction=transaction, tenant_id="tenant-1", clock=lambda: NOW)
    command = AgentRunCreate(project_id="project-1", idempotency_key="duplicate-result")
    linkage = replace(LINKAGE, session_id=None)
    first = await service.submit_run(command, linkage=linkage)
    result = await service.submit_run(
        command,
        linkage=linkage,
    )
    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert result.status is ControlPlaneWriteStatus.DUPLICATE
    assert all(item.duplicate for item in result.event_results)


@pytest.mark.asyncio
async def test_reused_command_key_with_changed_input_returns_conflict(transaction) -> None:
    service = DurableAgentControlPlane(transaction=transaction, tenant_id="tenant-1", clock=lambda: NOW)
    linkage = replace(LINKAGE, session_id=None)
    first = await service.submit_run(
        AgentRunCreate(project_id="project-1", idempotency_key="same-command", input_payload={"v": 1}),
        linkage=linkage,
    )
    second = await service.submit_run(
        AgentRunCreate(project_id="project-1", idempotency_key="same-command", input_payload={"v": 2}),
        linkage=linkage,
    )

    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert second.status is ControlPlaneWriteStatus.CONFLICT
    assert second.reason == "idempotency_key_payload_conflict"
    assert len(transaction.committed_writes) == 1
    assert first.run is not None
    assert transaction.aggregate_sequences[("tenant-1", "agent_run", first.run.id)] == 1


@pytest.mark.asyncio
async def test_mismatched_atomic_outbox_result_is_rejected(transaction) -> None:
    service = DurableAgentControlPlane(transaction=transaction, tenant_id="tenant-1", clock=lambda: NOW)
    transaction.invalid_result = True

    with pytest.raises(ControlPlaneResultError, match="event/outbox identity"):
        await service.submit_run(
            AgentRunCreate(project_id="project-1", idempotency_key="invalid-outbox-result"),
            linkage=replace(LINKAGE, session_id=None),
        )
