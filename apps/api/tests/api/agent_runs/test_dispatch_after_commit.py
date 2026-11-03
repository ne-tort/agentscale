"""Acceptance vectors: atomic intent write plus post-commit dispatch; not executed.

These tests bind the HTTP routes to the durable dispatcher contract without a
database:

* a committed create/cancel/approval writes its events, outbox projection and
  enqueue intents in one ``ControlPlaneAtomicWrite``;
* the consumer is triggered only after that write returned ``applied``/
  ``duplicate``, and only through the bounded ``dispatch_after_commit`` seam;
* an idempotent replay returns the original receipt and does not write again;
* a CAS rejection writes nothing and therefore has nothing to dispatch;
* an approve-vs-deny race has exactly one winner and one dispatch.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from prodavan.api.migration_agent_runs.dependencies import (
    INTENT_QUEUE_NAME,
    AgentRunBackend,
    dispatch_owner_id,
)
from prodavan.api.migration_agent_runs.schemas import (
    AgentRunCancelled,
    AgentRunCreated,
    AgentRunDispatchReport,
    AgentRunRecord,
)
from prodavan.api.v1 import agent_runs as agent_runs_api
from prodavan.application.agent_control_plane import (
    AtomicEventOutboxResult,
    ControlPlaneLinkage,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
    DurableAgentControlPlane,
)
from prodavan.application.agent_runs import DispatchBatchReport
from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    AgentRunView,
    ApprovalDecision,
    ApprovalRequestStatus,
    ApprovalRequestView,
)
from prodavan.domain.automation.outbox import OutboxEvent

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
TENANT = "tenant-dispatch"
PROJECT = "project-dispatch"
ACTOR = "employee-dispatch"
RUN = "agr_dispatch"
APPROVAL = "apr_dispatch"
TERMINAL = {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}


class CommitDouble:
    """Deterministic CAS/replay adapter; it never touches a database."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.writes = []
        self.committed = []
        self.sequences: dict[tuple[str, str], int] = {}
        self.statuses: dict[tuple[str, str], object] = {}
        self.receipts: dict[tuple[str, str], tuple[str, ControlPlaneWriteResult]] = {}

    def seed(self, aggregate_type: str, aggregate_id: str, sequence: int, status) -> None:
        identity = (aggregate_type, aggregate_id)
        self.sequences[identity] = sequence
        self.statuses[identity] = status

    async def commit(self, *, write) -> ControlPlaneWriteResult:
        self.log.append("commit")
        self.writes.append(write)
        command = write.command
        receipt_key = (command.operation, command.command_idempotency_key)
        previous = self.receipts.get(receipt_key)
        if previous is not None:
            digest, stored = previous
            if digest != command.command_digest:
                return ControlPlaneWriteResult(
                    status=ControlPlaneWriteStatus.CONFLICT,
                    reason="idempotency_key_payload_conflict",
                )
            return replace(
                stored,
                status=ControlPlaneWriteStatus.DUPLICATE,
                event_results=tuple(replace(item, duplicate=True) for item in stored.event_results),
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
                outbox_idempotency_key=OutboxEvent.aggregate_event_key(
                    tenant_id=TENANT,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    aggregate_version=event.sequence,
                ),
                queue_deduplication_key=intent.deduplication_key,
                event_recorded=True,
                queue_intent_recorded=True,
            )
            for event, intent in zip(write.events, write.outbox_intents, strict=True)
        )
        result = ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.APPLIED,
            event_results=event_results,
            run=_run_view(command),
            approval=_approval_view(command),
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


def _run_view(command) -> AgentRunView:
    return AgentRunView(
        id=command.run_id,
        project_id=command.linkage.project_id,
        session_id=command.linkage.session_id,
        requested_by_employee_id=command.linkage.actor_id,
        idempotency_key=command.command_idempotency_key,
        input_payload=dict(command.details.get("input_payload", {})),
        status=command.target_run_status,
        finished_at=NOW if command.target_run_status in TERMINAL else None,
        created_at=NOW,
        updated_at=NOW,
    )


def _approval_view(command) -> ApprovalRequestView | None:
    if command.approval_id is None:
        return None
    return ApprovalRequestView(
        id=command.approval_id,
        run_id=command.run_id,
        tool_call_id=str(command.details.get("tool_call_id", "tool-call")),
        approval_kind=str(command.details.get("approval_kind", "filesystem.write")),
        requested_payload=dict(command.details.get("requested_payload", {})),
        status=command.target_approval_status,
        decision_reason=command.details.get("reason"),
        decided_by_employee_id=command.linkage.actor_id,
        requested_at=NOW,
        decided_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


class RecordingDispatcher:
    """DispatchRunner double that also records the ordering with the commit."""

    def __init__(self, log: list[str], *, report: DispatchBatchReport | None = None) -> None:
        self.log = log
        self.calls: list[tuple[str, int]] = []
        self._report = report if report is not None else DispatchBatchReport(claimed=1, retried=1)

    async def dispatch_batch(self, *, owner_id: str, limit: int, repair_expired: int = 0):
        self.log.append("dispatch")
        self.calls.append((owner_id, limit))
        return self._report


def _linkage(*, key: str = "command-key") -> ControlPlaneLinkage:
    return ControlPlaneLinkage(
        tenant_id=TENANT,
        actor_id=ACTOR,
        project_id=PROJECT,
        session_id="session-dispatch",
        trace_id=f"agent-command:{key}",
    )


def _backend(double: CommitDouble, dispatcher) -> AgentRunBackend:
    return AgentRunBackend(
        transaction=double,
        session=_UnusedSession(),
        writes_enabled=True,
        dispatcher=dispatcher,
    )


class _UnusedSession:
    """The routes' read session is not used by these vectors."""


def _run_record() -> AgentRunRecord:
    return AgentRunRecord(
        id=RUN,
        project_id=PROJECT,
        session_id="session-dispatch",
        requested_by_employee_id=ACTOR,
        idempotency_key="create-key",
        status=AgentRunStatus.QUEUED,
        attempt=1,
        input_payload={"prompt": "run"},
        output_payload=None,
        failure_code=None,
        failure_message=None,
        sequence=1,
        started_at=None,
        heartbeat_at=None,
        finished_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


# --------------------------------------------------------------------------- #
# Atomic write: events, outbox and enqueue intents travel together
# --------------------------------------------------------------------------- #


def test_create_writes_event_outbox_and_queue_intent_in_one_atomic_write() -> None:
    double = CommitDouble()
    service = DurableAgentControlPlane(transaction=double, tenant_id=TENANT, clock=lambda: NOW)
    command = AgentRunCreate(
        project_id=PROJECT,
        session_id="session-dispatch",
        requested_by_employee_id=ACTOR,
        idempotency_key="create-key",
        input_payload={"prompt": "run"},
    )

    result = asyncio.run(service.submit_run(command, linkage=_linkage(), occurred_at=NOW))

    assert result.status is ControlPlaneWriteStatus.APPLIED
    assert len(double.writes) == 1
    write = double.writes[0]
    assert len(write.events) == len(write.outbox_events) == len(write.outbox_intents) == 1
    assert write.outbox_intents[0].event_id == str(write.events[0].event_id)
    assert write.outbox_intents[0].queue_name == INTENT_QUEUE_NAME
    assert write.outbox_intents[0].deduplication_key == write.outbox_events[0].idempotency_key
    assert write.command.aggregates[0].expected_sequence == 0
    assert result.event_results[0].queue_intent_recorded is True


def test_approval_decision_writes_two_events_and_two_intents_atomically() -> None:
    double = CommitDouble()
    double.seed("approval_request", APPROVAL, 1, ApprovalRequestStatus.PENDING)
    double.seed("agent_run", RUN, 4, AgentRunStatus.WAITING_APPROVAL)
    service = DurableAgentControlPlane(transaction=double, tenant_id=TENANT, clock=lambda: NOW)

    result = asyncio.run(
        service.decide_approval(
            APPROVAL,
            ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id=ACTOR),
            run_id=RUN,
            linkage=_linkage(key="approve-key"),
            expected_approval_sequence=1,
            expected_run_sequence=4,
            command_idempotency_key="approve-key",
            occurred_at=NOW,
        )
    )

    assert result.status is ControlPlaneWriteStatus.APPLIED
    write = double.writes[0]
    assert [event.aggregate_type for event in write.events] == ["approval_request", "agent_run"]
    assert [event.sequence for event in write.events] == [2, 5]
    assert len(write.outbox_intents) == 2
    assert all(
        intent.event_id == str(event.event_id)
        for intent, event in zip(write.outbox_intents, write.events, strict=True)
    )
    assert {intent.queue_name for intent in write.outbox_intents} == {INTENT_QUEUE_NAME}


# --------------------------------------------------------------------------- #
# Post-commit dispatch ordering and idempotent replay
# --------------------------------------------------------------------------- #


def test_create_commit_is_dispatched_only_after_the_write_returns() -> None:
    double = CommitDouble()
    log: list[str] = []
    dispatcher = RecordingDispatcher(log)
    backend = _backend(double, dispatcher)
    service = backend.control_plane(tenant_id=TENANT)
    command = AgentRunCreate(
        project_id=PROJECT,
        session_id="session-dispatch",
        requested_by_employee_id=ACTOR,
        idempotency_key="create-key",
        input_payload={"prompt": "run"},
    )

    async def create_once():
        result = await service.submit_run(command, linkage=_linkage(), occurred_at=NOW)
        report = await agent_runs_api._dispatch_committed(
            backend=backend, trace_id=_linkage().trace_id, result=result
        )
        return result, report

    result, report = asyncio.run(create_once())

    assert result.status is ControlPlaneWriteStatus.APPLIED
    assert log == ["commit", "dispatch"]
    assert dispatcher.calls == [(dispatch_owner_id(_linkage().trace_id), 1)]
    assert report.triggered is True
    assert (report.claimed, report.retried) == (1, 1)


def test_create_replay_returns_the_original_receipt_without_a_second_write() -> None:
    double = CommitDouble()
    log: list[str] = []
    dispatcher = RecordingDispatcher(log, report=DispatchBatchReport(claimed=1, delivered=1))
    backend = _backend(double, dispatcher)
    service = backend.control_plane(tenant_id=TENANT)
    command = AgentRunCreate(
        project_id=PROJECT,
        session_id="session-dispatch",
        requested_by_employee_id=ACTOR,
        idempotency_key="create-key",
        input_payload={"prompt": "run"},
    )

    async def create_twice():
        first = await service.submit_run(command, linkage=_linkage(), occurred_at=NOW)
        second = await service.submit_run(command, linkage=_linkage(), occurred_at=NOW)
        reports = [
            await agent_runs_api._dispatch_committed(
                backend=backend, trace_id=_linkage().trace_id, result=item
            )
            for item in (first, second)
        ]
        return first, second, reports

    first, replay, reports = asyncio.run(create_twice())

    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert replay.status is ControlPlaneWriteStatus.DUPLICATE
    assert replay.run == first.run
    assert replay.event_results[0].duplicate is True
    assert len(double.committed) == 1
    # The replay restores the same single intent; it is dispatched again (the
    # reservation makes that safe) but never written twice.
    assert [len(report.event_results) for report in (first, replay)] == [1, 1]
    assert len(double.writes) == 2
    assert [call[1] for call in dispatcher.calls] == [1, 1]
    assert all(report.triggered for report in reports)


def test_cancel_cas_rejection_writes_nothing_and_has_nothing_to_dispatch() -> None:
    double = CommitDouble()
    double.seed("agent_run", RUN, 6, AgentRunStatus.RUNNING)
    log: list[str] = []
    dispatcher = RecordingDispatcher(log)
    backend = _backend(double, dispatcher)
    service = backend.control_plane(tenant_id=TENANT)

    stale = asyncio.run(
        service.cancel_run(
            run_id=RUN,
            linkage=_linkage(key="cancel-key"),
            expected_sequence=5,
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-key",
            occurred_at=NOW,
        )
    )

    assert stale.status is ControlPlaneWriteStatus.CONFLICT
    assert stale.reason == "aggregate_sequence_conflict"
    assert double.committed == []
    assert log == ["commit"]
    assert dispatcher.calls == []

    fresh = asyncio.run(
        service.cancel_run(
            run_id=RUN,
            linkage=_linkage(key="cancel-key"),
            expected_sequence=6,
            expected_status=AgentRunStatus.RUNNING,
            command_idempotency_key="cancel-key",
            occurred_at=NOW,
        )
    )
    report = asyncio.run(
        agent_runs_api._dispatch_committed(
            backend=backend, trace_id=_linkage(key="cancel-key").trace_id, result=fresh
        )
    )

    assert fresh.status is ControlPlaneWriteStatus.APPLIED
    assert double.sequences[("agent_run", RUN)] == 7
    assert log == ["commit", "commit", "dispatch"]
    assert report.claimed == 1


def test_approve_vs_deny_race_has_one_winner_and_one_dispatch() -> None:
    double = CommitDouble()
    double.seed("approval_request", APPROVAL, 1, ApprovalRequestStatus.PENDING)
    double.seed("agent_run", RUN, 4, AgentRunStatus.WAITING_APPROVAL)
    log: list[str] = []
    dispatcher = RecordingDispatcher(log, report=DispatchBatchReport(claimed=2, retried=2))
    backend = _backend(double, dispatcher)
    service = backend.control_plane(tenant_id=TENANT)

    async def decide_both():
        return await asyncio.gather(
            service.decide_approval(
                APPROVAL,
                ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id=ACTOR),
                run_id=RUN,
                linkage=_linkage(key="approve-key"),
                expected_approval_sequence=1,
                expected_run_sequence=4,
                command_idempotency_key="approve-key",
                occurred_at=NOW,
            ),
            service.decide_approval(
                APPROVAL,
                ApprovalDecision(status=ApprovalRequestStatus.DENIED, decided_by_employee_id=ACTOR),
                run_id=RUN,
                linkage=_linkage(key="deny-key"),
                expected_approval_sequence=1,
                expected_run_sequence=4,
                command_idempotency_key="deny-key",
                occurred_at=NOW,
            ),
        )

    first, second = asyncio.run(decide_both())
    statuses = {first.status, second.status}

    assert statuses == {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.CONFLICT}
    assert len(double.committed) == 1
    winner = next(item for item in (first, second) if item.status is ControlPlaneWriteStatus.APPLIED)
    assert double.sequences[("approval_request", APPROVAL)] == 2
    assert double.sequences[("agent_run", RUN)] == 5
    assert dispatcher.calls == []  # nothing dispatched until the route asks

    report = asyncio.run(
        agent_runs_api._dispatch_committed(
            backend=backend, trace_id=_linkage(key="approve-key").trace_id, result=winner
        )
    )

    assert len(double.writes) == 2  # both peers attempted; only one committed
    assert len(double.committed[0].events) == len(double.committed[0].outbox_intents) == 2
    assert dispatcher.calls == [(dispatch_owner_id(_linkage(key="approve-key").trace_id), 2)]
    assert report.claimed == 2


# --------------------------------------------------------------------------- #
# Route wiring: every mutation dispatches, and only after a committed write
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("route_name", "commit_anchor"),
    [
        ("create_agent_run", "submit_run("),
        ("cancel_agent_run", "cancel_run("),
        ("decide_agent_approval", "decide_approval("),
        ("cancel_agent_approval", "cancel_approval("),
    ],
)
def test_every_mutation_dispatches_after_its_status_check(route_name: str, commit_anchor: str) -> None:
    source = inspect.getsource(getattr(agent_runs_api, route_name))
    assert commit_anchor in source
    assert "_dispatch_committed(" in source
    assert source.index(commit_anchor) < source.index("_dispatch_committed(")
    # A rejected/blocked command raises before anything is handed to a consumer.
    assert source.index("_write_error(result)") < source.index("_dispatch_committed(")


def test_mutation_responses_report_the_dispatch_outcome() -> None:
    report = AgentRunDispatchReport(triggered=True, claimed=1, retried=1)
    created = AgentRunCreated(
        run=_run_record(),
        duplicate=False,
        queue_intents_recorded=1,
        dispatch=report,
    )
    legacy = AgentRunCancelled(run=_run_record(), duplicate=True, queue_intents_recorded=1)

    assert created.dispatch is report
    assert created.dispatch.retried == 1
    assert legacy.dispatch is None  # older clients/payloads stay valid


def test_dispatch_report_rejects_negative_counters() -> None:
    with pytest.raises(ValueError):
        AgentRunDispatchReport(triggered=True, claimed=-1)
