"""Durable Agent Runs/Approvals control-plane migration contracts.

The domain models in ``prodavan.domain.agent.control_plane`` describe the
wire state of runs and approvals.  This module adds the application-level
command context that those models intentionally do not carry: actor, project,
session, and trace linkage; transition preconditions; and the result of one
atomic state/event/outbox transaction.

A command is only an intent.  The durable transaction port remains the source
of truth for compare-and-set state changes and aggregate sequence allocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import UUID

from prodavan.domain.agent.control_plane import (
    AgentRunStatus,
    AgentRunView,
    ApprovalRequestStatus,
    ApprovalRequestView,
    EventEnvelope,
)
from prodavan.domain.automation.outbox import OutboxEnqueueIntent, OutboxEvent


AggregateType = Literal["agent_run", "approval_request"]


def _required(value: str, name: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _optional_aware(value: datetime | None, name: str) -> datetime | None:
    return None if value is None else _aware(value, name)


class ControlPlaneWriteStatus(StrEnum):
    """Durable result of a control-plane transaction."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    BLOCKED = "blocked"
    FAILED = "failed"


class RunTransitionOperation(StrEnum):
    START = "start"
    WAIT_FOR_APPROVAL = "wait_for_approval"
    RESUME = "resume"
    SUCCEED = "succeed"
    FAIL = "fail"
    CANCEL = "cancel"
    TIMEOUT = "timeout"
    #: Append one ordered stream frame without changing the run status. The run
    #: keeps running (or waiting for approval) while its event log grows, which is
    #: what makes a durable ``(run_id, sequence)`` pair available to the SSE wire.
    STREAM_FRAME = "stream_frame"


class ApprovalTransitionOperation(StrEnum):
    APPROVE = "approve"
    DENY = "deny"
    CANCEL = "cancel"
    EXPIRE = "expire"


@dataclass(frozen=True, slots=True)
class ControlPlaneLinkage:
    """Identity that must survive every command, event, and queue intent."""

    tenant_id: str
    actor_id: str
    project_id: str
    session_id: str | None
    trace_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id", max_length=64))
        object.__setattr__(self, "actor_id", _required(self.actor_id, "actor_id", max_length=128))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id", max_length=64))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _required(self.session_id, "session_id", max_length=64))
        object.__setattr__(self, "trace_id", _required(self.trace_id, "trace_id", max_length=160))


@dataclass(frozen=True, slots=True)
class RunTransitionCommand:
    """One run compare-and-set transition with explicit aggregate version."""

    run_id: str
    linkage: ControlPlaneLinkage
    operation: RunTransitionOperation
    command_idempotency_key: str
    expected_status: AgentRunStatus
    target_status: AgentRunStatus
    expected_sequence: int
    reason: str | None = None
    failure_code: str | None = None
    output_payload: Mapping[str, Any] | None = None
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        _required(self.run_id, "run_id", max_length=64)
        _required(self.command_idempotency_key, "command_idempotency_key", max_length=512)
        if self.expected_sequence < 0:
            raise ValueError("expected_sequence must be non-negative")
        if self.occurred_at is not None:
            _aware(self.occurred_at, "occurred_at")
        if self.reason is not None and len(self.reason) > 2_000:
            raise ValueError("reason exceeds 2000 characters")
        if self.failure_code is not None:
            _required(self.failure_code, "failure_code", max_length=128)
        if self.output_payload is not None:
            object.__setattr__(self, "output_payload", _freeze_mapping(self.output_payload))
        validate_run_transition(self.expected_status, self.target_status, self.operation)


@dataclass(frozen=True, slots=True)
class ApprovalRequestCommand:
    """Create a pending approval and move its run to waiting_approval."""

    run_id: str
    tenant_id: str
    project_id: str
    session_id: str | None
    actor_id: str
    trace_id: str
    tool_call_id: str
    approval_kind: str
    command_idempotency_key: str
    expected_run_sequence: int
    expected_run_status: AgentRunStatus
    requested_payload: Mapping[str, Any]
    expires_at: datetime | None = None
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        _required(self.run_id, "run_id", max_length=64)
        _required(self.tenant_id, "tenant_id", max_length=64)
        _required(self.project_id, "project_id", max_length=64)
        if self.session_id is not None:
            _required(self.session_id, "session_id", max_length=64)
        _required(self.actor_id, "actor_id", max_length=128)
        _required(self.trace_id, "trace_id", max_length=160)
        _required(self.tool_call_id, "tool_call_id", max_length=128)
        _required(self.approval_kind, "approval_kind", max_length=64)
        _required(self.command_idempotency_key, "command_idempotency_key", max_length=512)
        if self.expected_run_sequence < 0:
            raise ValueError("expected_run_sequence must be non-negative")
        if self.expected_run_status not in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}:
            raise ValueError("approval request requires a queued or running run")
        _optional_aware(self.expires_at, "expires_at")
        _optional_aware(self.occurred_at, "occurred_at")
        object.__setattr__(self, "requested_payload", _freeze_mapping(self.requested_payload))


@dataclass(frozen=True, slots=True)
class ApprovalTransitionCommand:
    """One approval CAS transition, optionally advancing its linked run."""

    approval_id: str
    run_id: str
    tenant_id: str
    project_id: str
    session_id: str | None
    actor_id: str
    trace_id: str
    operation: ApprovalTransitionOperation
    command_idempotency_key: str
    expected_approval_sequence: int
    expected_status: ApprovalRequestStatus
    target_status: ApprovalRequestStatus
    expected_run_sequence: int
    expected_run_status: AgentRunStatus
    target_run_status: AgentRunStatus
    reason: str | None = None
    expires_at: datetime | None = None
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        _required(self.approval_id, "approval_id", max_length=64)
        _required(self.run_id, "run_id", max_length=64)
        _required(self.tenant_id, "tenant_id", max_length=64)
        _required(self.project_id, "project_id", max_length=64)
        if self.session_id is not None:
            _required(self.session_id, "session_id", max_length=64)
        _required(self.actor_id, "actor_id", max_length=128)
        _required(self.trace_id, "trace_id", max_length=160)
        _required(self.command_idempotency_key, "command_idempotency_key", max_length=512)
        if self.expected_approval_sequence < 0 or self.expected_run_sequence < 0:
            raise ValueError("aggregate sequences must be non-negative")
        _optional_aware(self.expires_at, "expires_at")
        _optional_aware(self.occurred_at, "occurred_at")
        if self.reason is not None and len(self.reason) > 2_000:
            raise ValueError("reason exceeds 2000 characters")
        validate_approval_transition(self.expected_status, self.target_status, self.operation)
        if self.target_status is ApprovalRequestStatus.APPROVED:
            if self.target_run_status is not AgentRunStatus.RUNNING:
                raise ValueError("approved approval must resume the run")
        elif self.target_status in {ApprovalRequestStatus.DENIED, ApprovalRequestStatus.EXPIRED}:
            if self.target_run_status is not AgentRunStatus.FAILED:
                raise ValueError("denied or expired approval must fail the run")
        elif self.target_status is ApprovalRequestStatus.CANCELLED:
            if self.target_run_status is not AgentRunStatus.CANCELLED:
                raise ValueError("cancelled approval must cancel the run")
        if self.expected_run_status is not AgentRunStatus.WAITING_APPROVAL:
            raise ValueError("approval transition requires a waiting_approval run")
        if self.operation is ApprovalTransitionOperation.EXPIRE and self.expires_at is None:
            raise ValueError("approval expiry transition requires expires_at")


@dataclass(frozen=True, slots=True)
class ControlPlaneAggregateVersion:
    """Expected and next sequence for one aggregate in one transaction."""

    aggregate_type: AggregateType
    aggregate_id: str
    expected_sequence: int
    next_sequence: int

    def __post_init__(self) -> None:
        _required(self.aggregate_id, "aggregate_id", max_length=64)
        if self.expected_sequence < 0 or self.next_sequence < 1:
            raise ValueError("aggregate sequences must be non-negative/positive")
        if self.next_sequence != self.expected_sequence + 1:
            raise ValueError("next_sequence must immediately follow expected_sequence")


@dataclass(frozen=True, slots=True)
class ControlPlaneWriteCommand:
    """Complete command passed to the repository-owned atomic transaction."""

    operation: str
    command_idempotency_key: str
    linkage: ControlPlaneLinkage
    run_id: str
    aggregates: tuple[ControlPlaneAggregateVersion, ...]
    expected_run_status: AgentRunStatus | None
    target_run_status: AgentRunStatus | None
    approval_id: str | None = None
    expected_approval_status: ApprovalRequestStatus | None = None
    target_approval_status: ApprovalRequestStatus | None = None
    details: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        _required(self.operation, "operation", max_length=128)
        _required(self.command_idempotency_key, "command_idempotency_key", max_length=512)
        _required(self.run_id, "run_id", max_length=64)
        if not self.aggregates:
            raise ValueError("at least one aggregate version is required")
        seen = set()
        for aggregate in self.aggregates:
            identity = (aggregate.aggregate_type, aggregate.aggregate_id)
            if identity in seen:
                raise ValueError("aggregate may occur only once in one command")
            seen.add(identity)
        if self.approval_id is not None:
            _required(self.approval_id, "approval_id", max_length=64)
        object.__setattr__(self, "details", _freeze_mapping(self.details))

    @property
    def command_digest(self) -> str:
        """Return the stable digest the durable port must bind to the key."""
        canonical = {
            "operation": self.operation,
            "command_idempotency_key": self.command_idempotency_key,
            "linkage": {
                "tenant_id": self.linkage.tenant_id,
                "actor_id": self.linkage.actor_id,
                "project_id": self.linkage.project_id,
                "session_id": self.linkage.session_id,
                "trace_id": self.linkage.trace_id,
            },
            "run_id": self.run_id,
            "aggregates": [
                {
                    "aggregate_type": aggregate.aggregate_type,
                    "aggregate_id": aggregate.aggregate_id,
                    "expected_sequence": aggregate.expected_sequence,
                    "next_sequence": aggregate.next_sequence,
                }
                for aggregate in self.aggregates
            ],
            "expected_run_status": self.expected_run_status.value if self.expected_run_status else None,
            "target_run_status": self.target_run_status.value if self.target_run_status else None,
            "approval_id": self.approval_id,
            "expected_approval_status": (
                self.expected_approval_status.value if self.expected_approval_status else None
            ),
            "target_approval_status": self.target_approval_status.value if self.target_approval_status else None,
            "details": _jsonable(self.details),
        }
        return sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ControlPlaneAtomicWrite:
    """One complete durable state/event/outbox write.

    The application service constructs this value, while the persistence
    adapter owns the transaction.  Keeping the event and queue rows together
    makes it impossible for a repository implementation to claim a successful
    state transition while dropping its delivery intent.
    """

    command: ControlPlaneWriteCommand
    events: tuple[EventEnvelope, ...]
    outbox_events: tuple[OutboxEvent, ...]
    outbox_intents: tuple[OutboxEnqueueIntent, ...]
    transition_idempotency_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.events:
            raise ValueError("an atomic control-plane write requires at least one event")
        if not (
            len(self.events) == len(self.outbox_events) == len(self.outbox_intents)
            == len(self.transition_idempotency_keys)
        ):
            raise ValueError("events, outbox rows, intents, and transition keys must have equal length")

        versions = {
            (aggregate.aggregate_type, aggregate.aggregate_id): aggregate
            for aggregate in self.command.aggregates
        }
        event_ids: set[str] = set()
        event_aggregates: set[tuple[str, str]] = set()
        transition_keys = set(self.transition_idempotency_keys)
        if len(transition_keys) != len(self.transition_idempotency_keys):
            raise ValueError("transition idempotency keys must be unique")

        for event, outbox_event, intent, transition_key in zip(
            self.events,
            self.outbox_events,
            self.outbox_intents,
            self.transition_idempotency_keys,
            strict=True,
        ):
            identity = (event.aggregate_type, event.aggregate_id)
            version = versions.get(identity)
            if version is None or event.sequence != version.next_sequence:
                raise ValueError("event sequence does not match the command's aggregate version")
            if identity in event_aggregates:
                raise ValueError("one atomic write cannot emit two events for one aggregate version")
            event_aggregates.add(identity)
            if str(event.event_id) in event_ids:
                raise ValueError("event IDs must be unique within one atomic write")
            event_ids.add(str(event.event_id))
            if event.idempotency_key != transition_key:
                raise ValueError("event idempotency key must match its transition key")
            _validate_event_linkage(event=event, command=self.command)

            expected_outbox_key = OutboxEvent.aggregate_event_key(
                tenant_id=self.command.linkage.tenant_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.sequence or 0,
            )
            if (
                outbox_event.event_id != str(event.event_id)
                or outbox_event.tenant_id != self.command.linkage.tenant_id
                or outbox_event.aggregate_type != event.aggregate_type
                or outbox_event.aggregate_id != event.aggregate_id
                or outbox_event.aggregate_version != event.sequence
                or outbox_event.event_type != event.event_type
                or outbox_event.payload != event.model_dump(mode="json")
                or outbox_event.idempotency_key != expected_outbox_key
            ):
                raise ValueError("outbox event must be an exact durable projection of the domain event")
            if intent.event_id != outbox_event.event_id or intent.deduplication_key != expected_outbox_key:
                raise ValueError("queue intent must reference the same aggregate event key")

        if self.command.approval_id is not None:
            approval_events = [event for event in self.events if event.aggregate_type == "approval_request"]
            if not approval_events or all(event.aggregate_id != self.command.approval_id for event in approval_events):
                raise ValueError("approval command must include its approval aggregate event")
        if event_aggregates != set(versions):
            raise ValueError("every changed aggregate must have exactly one committed event")


def _validate_event_linkage(*, event: EventEnvelope, command: ControlPlaneWriteCommand) -> None:
    if event.run_id != command.run_id or event.session_id != command.linkage.session_id:
        raise ValueError("event run/session linkage does not match the command")
    control_plane = event.payload.get("control_plane")
    if not isinstance(control_plane, Mapping):
        raise ValueError("event payload must carry control-plane linkage")
    expected = {
        "tenant_id": command.linkage.tenant_id,
        "actor_id": command.linkage.actor_id,
        "project_id": command.linkage.project_id,
        "session_id": command.linkage.session_id,
        "trace_id": command.linkage.trace_id,
        "command_idempotency_key": command.command_idempotency_key,
    }
    if any(control_plane.get(key) != value for key, value in expected.items()):
        raise ValueError("event actor/project/session/trace linkage does not match the command")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("command digest mappings must use string keys")
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class AtomicEventOutboxResult:
    """Result for one event and its queue intent after the same commit."""

    event_id: str
    aggregate_type: AggregateType
    aggregate_id: str
    aggregate_version: int
    outbox_idempotency_key: str
    queue_deduplication_key: str
    event_recorded: bool
    queue_intent_recorded: bool
    duplicate: bool = False

    def __post_init__(self) -> None:
        _required(self.event_id, "event_id", max_length=128)
        _required(self.aggregate_id, "aggregate_id", max_length=64)
        _required(self.outbox_idempotency_key, "outbox_idempotency_key", max_length=512)
        _required(self.queue_deduplication_key, "queue_deduplication_key", max_length=512)
        if self.aggregate_version < 1:
            raise ValueError("aggregate_version must be positive")
        if self.outbox_idempotency_key != self.queue_deduplication_key:
            raise ValueError("outbox event and queue intent must use the same aggregate idempotency key")
        if self.event_recorded != self.queue_intent_recorded:
            raise ValueError("event and queue intent must have one atomic result")
        if not self.event_recorded:
            raise ValueError("a successful atomic result must retain event and queue intent")


@dataclass(frozen=True, slots=True)
class ControlPlaneWriteResult:
    """Atomic state/event/outbox result returned by the durable adapter."""

    status: ControlPlaneWriteStatus
    event_results: tuple[AtomicEventOutboxResult, ...] = ()
    run: AgentRunView | None = None
    approval: ApprovalRequestView | None = None
    reason: str | None = None
    blocker: str | None = None

    def __post_init__(self) -> None:
        if self.status in {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.DUPLICATE}:
            if not self.event_results:
                raise ValueError("successful control-plane write must return event results")
            if self.run is None:
                raise ValueError("successful control-plane write must return the persisted run")
            if self.run.status in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
                if self.run.finished_at is None:
                    raise ValueError("terminal run result must include finished_at")
            if self.status is ControlPlaneWriteStatus.DUPLICATE and not all(
                result.duplicate for result in self.event_results
            ):
                raise ValueError("duplicate result must mark every event as duplicate")
            if self.status is ControlPlaneWriteStatus.APPLIED and any(
                result.duplicate for result in self.event_results
            ):
                raise ValueError("applied result cannot contain replayed event results")
        elif self.event_results:
            raise ValueError("failed control-plane write cannot return committed events")
        elif not (self.reason or self.blocker):
            raise ValueError("non-success control-plane result must provide a reason or blocker")
        if self.reason is not None and len(self.reason) > 512:
            raise ValueError("reason exceeds 512 characters")
        if self.blocker is not None:
            _required(self.blocker, "blocker", max_length=256)


# Compatibility aliases make the migration contract discoverable without
# coupling callers to one naming choice.
AgentRunTransitionCommand = RunTransitionCommand
AgentApprovalRequestCommand = ApprovalRequestCommand
AgentApprovalTransitionCommand = ApprovalTransitionCommand
AtomicControlPlaneResult = ControlPlaneWriteResult


def validate_run_transition(
    source: AgentRunStatus,
    target: AgentRunStatus,
    operation: RunTransitionOperation,
) -> None:
    allowed: dict[RunTransitionOperation, tuple[tuple[AgentRunStatus, AgentRunStatus], ...]] = {
        RunTransitionOperation.START: (
            (AgentRunStatus.QUEUED, AgentRunStatus.RUNNING),
            # A run parked with a durable ``retry_at`` is restarted by the same
            # operation; without this pair the retry path had no expressible
            # start transition and the claim predicate had to exclude the status.
            (AgentRunStatus.RETRY_DUE, AgentRunStatus.RUNNING),
        ),
        RunTransitionOperation.WAIT_FOR_APPROVAL: (
            (AgentRunStatus.QUEUED, AgentRunStatus.WAITING_APPROVAL),
            (AgentRunStatus.RUNNING, AgentRunStatus.WAITING_APPROVAL),
        ),
        RunTransitionOperation.RESUME: ((AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.RUNNING),),
        RunTransitionOperation.SUCCEED: ((AgentRunStatus.RUNNING, AgentRunStatus.SUCCEEDED),),
        RunTransitionOperation.FAIL: (
            (AgentRunStatus.QUEUED, AgentRunStatus.FAILED),
            (AgentRunStatus.RETRY_DUE, AgentRunStatus.FAILED),
            (AgentRunStatus.RUNNING, AgentRunStatus.FAILED),
        ),
        RunTransitionOperation.CANCEL: (
            (AgentRunStatus.QUEUED, AgentRunStatus.CANCELLED),
            (AgentRunStatus.RETRY_DUE, AgentRunStatus.CANCELLED),
            (AgentRunStatus.RUNNING, AgentRunStatus.CANCELLED),
        ),
        RunTransitionOperation.TIMEOUT: (
            (AgentRunStatus.QUEUED, AgentRunStatus.FAILED),
            (AgentRunStatus.RETRY_DUE, AgentRunStatus.FAILED),
            (AgentRunStatus.RUNNING, AgentRunStatus.FAILED),
        ),
        # Status-preserving append: the run stays in the state the frame arrived in.
        RunTransitionOperation.STREAM_FRAME: (
            (AgentRunStatus.RUNNING, AgentRunStatus.RUNNING),
            (AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_APPROVAL),
        ),
    }
    if (source, target) not in allowed[operation]:
        raise ValueError(f"invalid run transition: {source.value}->{target.value} for {operation.value}")


def validate_approval_transition(
    source: ApprovalRequestStatus,
    target: ApprovalRequestStatus,
    operation: ApprovalTransitionOperation,
) -> None:
    if source is not ApprovalRequestStatus.PENDING:
        raise ValueError("only pending approval requests may transition")
    expected = {
        ApprovalTransitionOperation.APPROVE: ApprovalRequestStatus.APPROVED,
        ApprovalTransitionOperation.DENY: ApprovalRequestStatus.DENIED,
        ApprovalTransitionOperation.CANCEL: ApprovalRequestStatus.CANCELLED,
        ApprovalTransitionOperation.EXPIRE: ApprovalRequestStatus.EXPIRED,
    }[operation]
    if target is not expected:
        raise ValueError(f"invalid approval transition: {source.value}->{target.value} for {operation.value}")


__all__ = [
    "AgentApprovalRequestCommand",
    "AgentApprovalTransitionCommand",
    "AgentRunTransitionCommand",
    "AggregateType",
    "AtomicControlPlaneResult",
    "ControlPlaneAtomicWrite",
    "AtomicEventOutboxResult",
    "ApprovalRequestCommand",
    "ApprovalTransitionCommand",
    "ApprovalTransitionOperation",
    "ControlPlaneAggregateVersion",
    "ControlPlaneLinkage",
    "ControlPlaneWriteCommand",
    "ControlPlaneWriteResult",
    "ControlPlaneWriteStatus",
    "RunTransitionCommand",
    "RunTransitionOperation",
    "validate_approval_transition",
    "validate_run_transition",
]
