"""Durable application service for Agent Runs/Approvals control-plane writes.

Every mutating method builds one immutable command and sends one
``ControlPlaneAtomicWrite`` to the repository-owned transaction port. The
service deliberately does not compose independent run, approval, and outbox
repositories: doing so would permit a committed state transition with a
missing event or queue intent. Until the durable adapter is wired, methods
return a visible ``blocked`` result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Callable, Literal, Mapping, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestStatus,
    EventEnvelope,
)
from prodavan.domain.automation.outbox import OutboxEnqueueIntent, OutboxEvent

from .contracts import (
    AggregateType,
    ApprovalRequestCommand,
    ApprovalTransitionCommand,
    ApprovalTransitionOperation,
    ControlPlaneAggregateVersion,
    ControlPlaneAtomicWrite,
    ControlPlaneLinkage,
    ControlPlaneWriteCommand,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
    RunTransitionCommand,
    RunTransitionOperation,
)
from .ports import AgentControlPlaneTransactionPort, WIRING_BLOCKER


Clock = Callable[[], datetime]


class ControlPlaneWriteError(RuntimeError):
    """The durable adapter failed; no fallback write is attempted."""

    def __init__(self, *, operation: str, command_idempotency_key: str, cause: BaseException) -> None:
        self.operation = operation
        self.command_idempotency_key = command_idempotency_key
        self.cause = cause
        error_type = f"{cause.__class__.__module__}.{cause.__class__.__qualname__}"
        super().__init__(f"agent_control_plane.atomic_write_failed:{operation}:{error_type}")


class ControlPlaneResultError(RuntimeError):
    """The durable adapter returned a result that does not match its command."""


class DurableAgentControlPlane:
    """Build and submit durable run/approval/event transitions.

    The constructor accepts only the new atomic transaction seam. ``None`` is
    allowed so deployments can expose an explicit migration blocker instead of
    silently reverting to process memory, separate repositories, or the legacy
    outbox service.
    """

    def __init__(
        self,
        *,
        transaction: AgentControlPlaneTransactionPort | None = None,
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
        if transaction is not None and not callable(getattr(transaction, "commit", None)):
            raise TypeError("transaction must expose async commit(write=...)")
        self._transaction = transaction
        self._tenant_id = tenant_id.strip()
        self._queue_name = queue_name.strip()
        self._producer = producer.strip()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def is_wired(self) -> bool:
        return self._transaction is not None

    @property
    def wiring_blocker(self) -> str | None:
        return None if self.is_wired else WIRING_BLOCKER

    async def submit_run(
        self,
        command: AgentRunCreate,
        *,
        linkage: ControlPlaneLinkage,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        """Create a queued run and its first ordered event atomically."""
        self._validate_run_linkage(command, linkage)
        run_id = _stable_id("agr", self._tenant_id, linkage.project_id, command.idempotency_key)
        write_command = ControlPlaneWriteCommand(
            operation="agent_run.create",
            command_idempotency_key=command.idempotency_key,
            linkage=linkage,
            run_id=run_id,
            aggregates=(
                ControlPlaneAggregateVersion(
                    aggregate_type="agent_run",
                    aggregate_id=run_id,
                    expected_sequence=0,
                    next_sequence=1,
                ),
            ),
            expected_run_status=None,
            target_run_status=AgentRunStatus.QUEUED,
            details={
                "input_payload": dict(command.input_payload),
                "requested_by_employee_id": command.requested_by_employee_id,
                "session_id": command.session_id,
            },
        )
        event = self._event(
            command=write_command,
            event_type="agent_run.created",
            aggregate_type="agent_run",
            aggregate_id=run_id,
            sequence=1,
            transition="created",
            occurred_at=occurred_at,
            payload={
                "project_id": command.project_id,
                "input_payload": dict(command.input_payload),
                "requested_by_employee_id": command.requested_by_employee_id,
                "status": AgentRunStatus.QUEUED.value,
            },
        )
        return await self._commit(self._atomic(write_command, (event,)))

    async def transition_run(self, command: RunTransitionCommand) -> ControlPlaneWriteResult:
        """Apply one run CAS transition, including cancellation and timeout."""
        self._validate_tenant(command.linkage)
        write_command = ControlPlaneWriteCommand(
            operation=f"agent_run.{command.operation.value}",
            command_idempotency_key=command.command_idempotency_key,
            linkage=command.linkage,
            run_id=command.run_id,
            aggregates=(
                ControlPlaneAggregateVersion(
                    aggregate_type="agent_run",
                    aggregate_id=command.run_id,
                    expected_sequence=command.expected_sequence,
                    next_sequence=command.expected_sequence + 1,
                ),
            ),
            expected_run_status=command.expected_status,
            target_run_status=command.target_status,
            details={
                "reason": command.reason,
                "failure_code": command.failure_code,
                "output_payload": command.output_payload,
            },
        )
        event_name = {
            RunTransitionOperation.START: "agent_run.started",
            RunTransitionOperation.WAIT_FOR_APPROVAL: "agent_run.waiting_approval",
            RunTransitionOperation.RESUME: "agent_run.resumed",
            RunTransitionOperation.SUCCEED: "agent_run.succeeded",
            RunTransitionOperation.FAIL: "agent_run.failed",
            RunTransitionOperation.CANCEL: "agent_run.cancelled",
            RunTransitionOperation.TIMEOUT: "agent_run.timed_out",
        }[command.operation]
        event = self._event(
            command=write_command,
            event_type=event_name,
            aggregate_type="agent_run",
            aggregate_id=command.run_id,
            sequence=command.expected_sequence + 1,
            transition=command.operation.value,
            occurred_at=command.occurred_at,
            payload={
                "from_status": command.expected_status.value,
                "status": command.target_status.value,
                "reason": command.reason,
                "failure_code": command.failure_code,
                "output_payload": command.output_payload,
            },
        )
        return await self._commit(self._atomic(write_command, (event,)))

    async def start_run(
        self,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_sequence: int,
        command_idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_run(
            RunTransitionCommand(
                run_id=run_id,
                linkage=linkage,
                operation=RunTransitionOperation.START,
                command_idempotency_key=command_idempotency_key,
                expected_status=AgentRunStatus.QUEUED,
                target_status=AgentRunStatus.RUNNING,
                expected_sequence=expected_sequence,
                occurred_at=occurred_at,
            )
        )

    async def wait_for_approval(
        self,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        command_idempotency_key: str,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_run(
            RunTransitionCommand(
                run_id=run_id,
                linkage=linkage,
                operation=RunTransitionOperation.WAIT_FOR_APPROVAL,
                command_idempotency_key=command_idempotency_key,
                expected_status=expected_status,
                target_status=AgentRunStatus.WAITING_APPROVAL,
                expected_sequence=expected_sequence,
                reason=reason,
                occurred_at=occurred_at,
            )
        )

    async def resume_run(
        self,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_sequence: int,
        command_idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_run(
            RunTransitionCommand(
                run_id=run_id,
                linkage=linkage,
                operation=RunTransitionOperation.RESUME,
                command_idempotency_key=command_idempotency_key,
                expected_status=AgentRunStatus.WAITING_APPROVAL,
                target_status=AgentRunStatus.RUNNING,
                expected_sequence=expected_sequence,
                occurred_at=occurred_at,
            )
        )

    async def succeed_run(
        self,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_sequence: int,
        command_idempotency_key: str,
        output_payload: Mapping[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_run(
            RunTransitionCommand(
                run_id=run_id,
                linkage=linkage,
                operation=RunTransitionOperation.SUCCEED,
                command_idempotency_key=command_idempotency_key,
                expected_status=AgentRunStatus.RUNNING,
                target_status=AgentRunStatus.SUCCEEDED,
                expected_sequence=expected_sequence,
                output_payload=output_payload,
                occurred_at=occurred_at,
            )
        )

    async def fail_run(
        self,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        command_idempotency_key: str,
        failure_code: str = "agent_run.failed",
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_run(
            RunTransitionCommand(
                run_id=run_id,
                linkage=linkage,
                operation=RunTransitionOperation.FAIL,
                command_idempotency_key=command_idempotency_key,
                expected_status=expected_status,
                target_status=AgentRunStatus.FAILED,
                expected_sequence=expected_sequence,
                failure_code=failure_code,
                reason=reason,
                occurred_at=occurred_at,
            )
        )

    async def cancel_run(
        self,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        command_idempotency_key: str,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_run(
            RunTransitionCommand(
                run_id=run_id,
                linkage=linkage,
                operation=RunTransitionOperation.CANCEL,
                command_idempotency_key=command_idempotency_key,
                expected_status=expected_status,
                target_status=AgentRunStatus.CANCELLED,
                expected_sequence=expected_sequence,
                reason=reason,
                occurred_at=occurred_at,
            )
        )

    async def timeout_run(
        self,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        command_idempotency_key: str,
        reason: str = "agent_run.timeout",
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_run(
            RunTransitionCommand(
                run_id=run_id,
                linkage=linkage,
                operation=RunTransitionOperation.TIMEOUT,
                command_idempotency_key=command_idempotency_key,
                expected_status=expected_status,
                target_status=AgentRunStatus.FAILED,
                expected_sequence=expected_sequence,
                failure_code="agent_run.timeout",
                reason=reason,
                occurred_at=occurred_at,
            )
        )

    async def request_approval(
        self,
        command: ApprovalRequestCreate,
        *,
        linkage: ControlPlaneLinkage,
        expected_run_sequence: int,
        expected_run_status: AgentRunStatus,
        command_idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        """Create an approval and move its run to waiting_approval atomically."""
        self._validate_tenant(linkage)
        timestamp = self._timestamp(occurred_at)
        _validate_aware(expires_at=command.expires_at)
        if command.expires_at is not None and command.expires_at <= timestamp:
            raise ValueError("approval expiry must be in the future")
        key = command_idempotency_key or f"approval-request:{command.run_id}:{command.tool_call_id}"
        approval_id = _stable_id("apr", self._tenant_id, command.run_id, command.tool_call_id)
        ApprovalRequestCommand(
            run_id=command.run_id,
            tenant_id=self._tenant_id,
            project_id=linkage.project_id,
            session_id=linkage.session_id,
            actor_id=linkage.actor_id,
            trace_id=linkage.trace_id,
            tool_call_id=command.tool_call_id,
            approval_kind=command.approval_kind,
            command_idempotency_key=key,
            expected_run_sequence=expected_run_sequence,
            expected_run_status=expected_run_status,
            requested_payload=command.requested_payload,
            expires_at=command.expires_at,
            occurred_at=occurred_at,
        )
        write_command = ControlPlaneWriteCommand(
            operation="approval_request.create",
            command_idempotency_key=key,
            linkage=linkage,
            run_id=command.run_id,
            aggregates=(
                ControlPlaneAggregateVersion(
                    aggregate_type="agent_run",
                    aggregate_id=command.run_id,
                    expected_sequence=expected_run_sequence,
                    next_sequence=expected_run_sequence + 1,
                ),
                ControlPlaneAggregateVersion(
                    aggregate_type="approval_request",
                    aggregate_id=approval_id,
                    expected_sequence=0,
                    next_sequence=1,
                ),
            ),
            expected_run_status=expected_run_status,
            target_run_status=AgentRunStatus.WAITING_APPROVAL,
            approval_id=approval_id,
            target_approval_status=ApprovalRequestStatus.PENDING,
            details={
                "tool_call_id": command.tool_call_id,
                "approval_kind": command.approval_kind,
                "requested_payload": dict(command.requested_payload),
                "expires_at": command.expires_at.isoformat() if command.expires_at else None,
            },
        )
        run_event = self._event(
            command=write_command,
            event_type="agent_run.waiting_approval",
            aggregate_type="agent_run",
            aggregate_id=command.run_id,
            sequence=expected_run_sequence + 1,
            transition="waiting_approval",
            occurred_at=timestamp,
            payload={
                "from_status": expected_run_status.value,
                "status": AgentRunStatus.WAITING_APPROVAL.value,
                "approval_id": approval_id,
                "tool_call_id": command.tool_call_id,
            },
        )
        approval_event = self._event(
            command=write_command,
            event_type="approval_request.created",
            aggregate_type="approval_request",
            aggregate_id=approval_id,
            sequence=1,
            transition="approval_created",
            occurred_at=timestamp,
            payload={
                "approval_id": approval_id,
                "tool_call_id": command.tool_call_id,
                "approval_kind": command.approval_kind,
                "requested_payload": dict(command.requested_payload),
                "status": ApprovalRequestStatus.PENDING.value,
                "expires_at": command.expires_at.isoformat() if command.expires_at else None,
            },
        )
        return await self._commit(self._atomic(write_command, (run_event, approval_event)))

    async def transition_approval(self, command: ApprovalTransitionCommand) -> ControlPlaneWriteResult:
        """Apply approval decision/cancel/expiry plus the linked run CAS."""
        timestamp = self._timestamp(command.occurred_at)
        _validate_aware(expires_at=command.expires_at)
        if command.operation is ApprovalTransitionOperation.EXPIRE:
            if command.expires_at is None:
                raise ValueError("approval expiry transition requires expires_at")
            if command.expires_at > timestamp:
                raise ValueError("approval cannot expire before expires_at")
        linkage = ControlPlaneLinkage(
            tenant_id=command.tenant_id,
            actor_id=command.actor_id,
            project_id=command.project_id,
            session_id=command.session_id,
            trace_id=command.trace_id,
        )
        self._validate_tenant(linkage)
        write_command = ControlPlaneWriteCommand(
            operation=f"approval_request.{command.operation.value}",
            command_idempotency_key=command.command_idempotency_key,
            linkage=linkage,
            run_id=command.run_id,
            aggregates=(
                ControlPlaneAggregateVersion(
                    aggregate_type="approval_request",
                    aggregate_id=command.approval_id,
                    expected_sequence=command.expected_approval_sequence,
                    next_sequence=command.expected_approval_sequence + 1,
                ),
                ControlPlaneAggregateVersion(
                    aggregate_type="agent_run",
                    aggregate_id=command.run_id,
                    expected_sequence=command.expected_run_sequence,
                    next_sequence=command.expected_run_sequence + 1,
                ),
            ),
            expected_run_status=command.expected_run_status,
            target_run_status=command.target_run_status,
            approval_id=command.approval_id,
            expected_approval_status=command.expected_status,
            target_approval_status=command.target_status,
            details={
                "reason": command.reason,
                "expires_at": command.expires_at.isoformat() if command.expires_at else None,
            },
        )
        approval_event_name = {
            ApprovalTransitionOperation.APPROVE: "approval_request.approved",
            ApprovalTransitionOperation.DENY: "approval_request.denied",
            ApprovalTransitionOperation.CANCEL: "approval_request.cancelled",
            ApprovalTransitionOperation.EXPIRE: "approval_request.expired",
        }[command.operation]
        approval_event = self._event(
            command=write_command,
            event_type=approval_event_name,
            aggregate_type="approval_request",
            aggregate_id=command.approval_id,
            sequence=command.expected_approval_sequence + 1,
            transition=command.operation.value,
            occurred_at=timestamp,
            payload={
                "from_status": command.expected_status.value,
                "status": command.target_status.value,
                "run_id": command.run_id,
                "reason": command.reason,
            },
        )
        run_event_name = {
            ApprovalTransitionOperation.APPROVE: "agent_run.resumed",
            ApprovalTransitionOperation.DENY: "agent_run.failed",
            ApprovalTransitionOperation.CANCEL: "agent_run.cancelled",
            ApprovalTransitionOperation.EXPIRE: "agent_run.timed_out",
        }[command.operation]
        run_event = self._event(
            command=write_command,
            event_type=run_event_name,
            aggregate_type="agent_run",
            aggregate_id=command.run_id,
            sequence=command.expected_run_sequence + 1,
            transition=f"run_{command.operation.value}",
            occurred_at=timestamp,
            payload={
                "from_status": command.expected_run_status.value,
                "status": command.target_run_status.value,
                "approval_id": command.approval_id,
                "reason": command.reason,
                "failure_code": (
                    "approval.denied"
                    if command.operation is ApprovalTransitionOperation.DENY
                    else "approval.expired"
                    if command.operation is ApprovalTransitionOperation.EXPIRE
                    else None
                ),
            },
        )
        return await self._commit(self._atomic(write_command, (approval_event, run_event)))

    async def decide_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_approval_sequence: int,
        expected_run_sequence: int,
        command_idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        """Approve or deny a pending approval with one linked run CAS."""
        if decision.decided_by_employee_id != linkage.actor_id:
            raise ValueError("approval actor must match the authenticated linkage actor")
        operation = (
            ApprovalTransitionOperation.APPROVE
            if decision.status is ApprovalRequestStatus.APPROVED
            else ApprovalTransitionOperation.DENY
        )
        return await self.transition_approval(
            ApprovalTransitionCommand(
                approval_id=approval_id,
                run_id=run_id,
                tenant_id=self._tenant_id,
                project_id=linkage.project_id,
                session_id=linkage.session_id,
                actor_id=linkage.actor_id,
                trace_id=linkage.trace_id,
                operation=operation,
                command_idempotency_key=command_idempotency_key or f"approval:{approval_id}:{operation.value}",
                expected_approval_sequence=expected_approval_sequence,
                expected_status=ApprovalRequestStatus.PENDING,
                target_status=decision.status,
                expected_run_sequence=expected_run_sequence,
                expected_run_status=AgentRunStatus.WAITING_APPROVAL,
                target_run_status=(
                    AgentRunStatus.RUNNING
                    if operation is ApprovalTransitionOperation.APPROVE
                    else AgentRunStatus.FAILED
                ),
                reason=decision.decision_reason,
                occurred_at=occurred_at,
            )
        )

    async def cancel_approval(
        self,
        *,
        approval_id: str,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_approval_sequence: int,
        expected_run_sequence: int,
        command_idempotency_key: str | None = None,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        return await self.transition_approval(
            ApprovalTransitionCommand(
                approval_id=approval_id,
                run_id=run_id,
                tenant_id=self._tenant_id,
                project_id=linkage.project_id,
                session_id=linkage.session_id,
                actor_id=linkage.actor_id,
                trace_id=linkage.trace_id,
                operation=ApprovalTransitionOperation.CANCEL,
                command_idempotency_key=command_idempotency_key or f"approval:{approval_id}:cancel",
                expected_approval_sequence=expected_approval_sequence,
                expected_status=ApprovalRequestStatus.PENDING,
                target_status=ApprovalRequestStatus.CANCELLED,
                expected_run_sequence=expected_run_sequence,
                expected_run_status=AgentRunStatus.WAITING_APPROVAL,
                target_run_status=AgentRunStatus.CANCELLED,
                reason=reason,
                occurred_at=occurred_at,
            )
        )

    async def expire_approval(
        self,
        *,
        approval_id: str,
        run_id: str,
        linkage: ControlPlaneLinkage,
        expected_approval_sequence: int,
        expected_run_sequence: int,
        expires_at: datetime,
        command_idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ControlPlaneWriteResult:
        timestamp = self._timestamp(occurred_at)
        _validate_aware(expires_at=expires_at)
        if expires_at > timestamp:
            raise ValueError("approval cannot expire before expires_at")
        return await self.transition_approval(
            ApprovalTransitionCommand(
                approval_id=approval_id,
                run_id=run_id,
                tenant_id=self._tenant_id,
                project_id=linkage.project_id,
                session_id=linkage.session_id,
                actor_id=linkage.actor_id,
                trace_id=linkage.trace_id,
                operation=ApprovalTransitionOperation.EXPIRE,
                command_idempotency_key=command_idempotency_key or f"approval:{approval_id}:expire",
                expected_approval_sequence=expected_approval_sequence,
                expected_status=ApprovalRequestStatus.PENDING,
                target_status=ApprovalRequestStatus.EXPIRED,
                expected_run_sequence=expected_run_sequence,
                expected_run_status=AgentRunStatus.WAITING_APPROVAL,
                target_run_status=AgentRunStatus.FAILED,
                reason="approval.expired",
                expires_at=expires_at,
                occurred_at=occurred_at,
            )
        )

    def _event(
        self,
        *,
        command: ControlPlaneWriteCommand,
        event_type: str,
        aggregate_type: AggregateType,
        aggregate_id: str,
        sequence: int,
        transition: str,
        occurred_at: datetime | None,
        payload: Mapping[str, Any],
    ) -> EventEnvelope:
        timestamp = self._timestamp(occurred_at)
        transition_key = _transition_key(
            tenant_id=command.linkage.tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            sequence=sequence,
            command_key=command.command_idempotency_key,
            transition=transition,
        )
        control_plane = {
            "tenant_id": command.linkage.tenant_id,
            "actor_id": command.linkage.actor_id,
            "project_id": command.linkage.project_id,
            "session_id": command.linkage.session_id,
            "trace_id": command.linkage.trace_id,
            "command_idempotency_key": command.command_idempotency_key,
        }
        body = _json_payload(dict(payload))
        body["control_plane"] = control_plane
        return EventEnvelope(
            event_id=_event_id(
                tenant_id=self._tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                sequence=sequence,
                transition_key=transition_key,
            ),
            event_type=event_type,
            occurred_at=timestamp,
            producer=self._producer,
            aggregate_type=cast("Literal['agent_run', 'approval_request', 'agent_session']", aggregate_type),
            aggregate_id=aggregate_id,
            run_id=command.run_id,
            session_id=command.linkage.session_id,
            correlation_id=_correlation_id(command.command_idempotency_key),
            idempotency_key=transition_key,
            sequence=sequence,
            payload=body,
        )

    def _atomic(self, command: ControlPlaneWriteCommand, events: tuple[EventEnvelope, ...]) -> ControlPlaneAtomicWrite:
        outbox_events: list[OutboxEvent] = []
        intents: list[OutboxEnqueueIntent] = []
        transition_keys: list[str] = []
        for event in events:
            sequence = event.sequence
            if sequence is None:
                raise ControlPlaneResultError("event sequence is required before outbox projection")
            aggregate_key = OutboxEvent.aggregate_event_key(
                tenant_id=self._tenant_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=sequence,
            )
            outbox_event = OutboxEvent(
                event_id=str(event.event_id),
                tenant_id=self._tenant_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=sequence,
                event_type=event.event_type,
                payload=event.model_dump(mode="json"),
                occurred_at=event.occurred_at,
                idempotency_key=aggregate_key,
            )
            outbox_events.append(outbox_event)
            intents.append(
                OutboxEnqueueIntent(
                    event_id=outbox_event.event_id,
                    queue_name=self._queue_name,
                    deduplication_key=aggregate_key,
                )
            )
            transition_keys.append(event.idempotency_key or "")
        return ControlPlaneAtomicWrite(
            command=command,
            events=events,
            outbox_events=tuple(outbox_events),
            outbox_intents=tuple(intents),
            transition_idempotency_keys=tuple(transition_keys),
        )

    async def _commit(self, write: ControlPlaneAtomicWrite) -> ControlPlaneWriteResult:
        if self._transaction is None:
            return ControlPlaneWriteResult(
                status=ControlPlaneWriteStatus.BLOCKED,
                blocker=WIRING_BLOCKER,
                reason="durable agent control-plane transaction port is not wired",
            )
        try:
            result = await self._transaction.commit(write=write)
        except Exception as exc:
            raise ControlPlaneWriteError(
                operation=write.command.operation,
                command_idempotency_key=write.command.command_idempotency_key,
                cause=exc,
            ) from exc
        if not isinstance(result, ControlPlaneWriteResult):
            raise ControlPlaneResultError("transaction port returned an invalid control-plane result")
        if result.status in {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.DUPLICATE}:
            if len(result.event_results) != len(write.events):
                raise ControlPlaneResultError("transaction result event count does not match atomic write")
            if result.run is None:
                raise ControlPlaneResultError("transaction result omitted the persisted run view")
            if (
                result.run.id != write.command.run_id
                or result.run.project_id != write.command.linkage.project_id
                or result.run.session_id != write.command.linkage.session_id
                or result.run.status != write.command.target_run_status
            ):
                raise ControlPlaneResultError("transaction result run linkage/status does not match the command")
            if write.command.approval_id is not None:
                if result.approval is None:
                    raise ControlPlaneResultError("approval transaction result omitted the persisted approval view")
                if (
                    result.approval.id != write.command.approval_id
                    or result.approval.run_id != write.command.run_id
                    or result.approval.status != write.command.target_approval_status
                ):
                    raise ControlPlaneResultError("transaction result approval linkage/status does not match the command")
            for expected_event, actual in zip(write.events, result.event_results, strict=True):
                if (
                    actual.event_id != str(expected_event.event_id)
                    or actual.aggregate_type != expected_event.aggregate_type
                    or actual.aggregate_id != expected_event.aggregate_id
                    or actual.aggregate_version != expected_event.sequence
                    or actual.outbox_idempotency_key
                    != OutboxEvent.aggregate_event_key(
                        tenant_id=self._tenant_id,
                        aggregate_type=expected_event.aggregate_type,
                        aggregate_id=expected_event.aggregate_id,
                        aggregate_version=expected_event.sequence or 0,
                    )
                    or actual.queue_deduplication_key
                    != OutboxEvent.aggregate_event_key(
                        tenant_id=self._tenant_id,
                        aggregate_type=expected_event.aggregate_type,
                        aggregate_id=expected_event.aggregate_id,
                        aggregate_version=expected_event.sequence or 0,
                    )
                ):
                    raise ControlPlaneResultError("transaction result event/outbox identity does not match atomic write")
        return result

    def _timestamp(self, occurred_at: datetime | None) -> datetime:
        timestamp = occurred_at or self._clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("control-plane timestamps must be timezone-aware")
        return timestamp

    def _validate_tenant(self, linkage: ControlPlaneLinkage) -> None:
        if linkage.tenant_id != self._tenant_id:
            raise ValueError("command tenant does not match control-plane tenant")

    def _validate_run_linkage(self, command: AgentRunCreate, linkage: ControlPlaneLinkage) -> None:
        self._validate_tenant(linkage)
        if command.project_id != linkage.project_id:
            raise ValueError("run project_id does not match control-plane linkage")
        if command.session_id != linkage.session_id:
            raise ValueError("run session_id does not match control-plane linkage")
        if command.requested_by_employee_id is not None and command.requested_by_employee_id != linkage.actor_id:
            raise ValueError("requested_by_employee_id does not match control-plane actor_id")


def _stable_id(prefix: str, *parts: str) -> str:
    canonical = "\x00".join(f"{len(part)}={part}" for part in parts)
    return f"{prefix}_{sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


def _transition_key(
    *,
    tenant_id: str,
    aggregate_type: AggregateType,
    aggregate_id: str,
    sequence: int,
    command_key: str,
    transition: str,
) -> str:
    digest = sha256(
        f"{tenant_id}\x00{aggregate_type}\x00{aggregate_id}\x00{sequence}\x00{command_key}\x00{transition}".encode("utf-8")
    ).hexdigest()
    return f"agent-transition:{digest}"


def _event_id(
    *, tenant_id: str, aggregate_type: str, aggregate_id: str, sequence: int, transition_key: str
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"prodavan://agent-control-plane/{tenant_id}/{aggregate_type}/{aggregate_id}/{sequence}/{transition_key}",
    )


def _correlation_id(command_key: str) -> UUID:
    return UUID(bytes=sha256(f"correlation\x00{command_key}".encode("utf-8")).digest()[:16])


def _validate_aware(*, expires_at: datetime | None) -> None:
    if expires_at is not None and (expires_at.tzinfo is None or expires_at.utcoffset() is None):
        raise ValueError("expires_at must be timezone-aware")


def _json_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("event payload keys must be strings")
        return {key: _json_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        _validate_aware(expires_at=value)
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"event payload contains a non-JSON value: {type(value).__name__}")


__all__ = ["ControlPlaneResultError", "ControlPlaneWriteError", "DurableAgentControlPlane"]
