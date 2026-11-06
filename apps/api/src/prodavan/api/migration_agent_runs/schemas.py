"""Typed employee-facing schemas for durable Agent Runs and approvals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from prodavan.domain.agent.control_plane import (
    AgentRunStatus,
    ApprovalRequestStatus,
    EventEnvelope,
)


class CreateAgentRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, max_length=64)
    input_payload: dict[str, Any] = Field(default_factory=dict)


class CancelAgentRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_sequence: int = Field(ge=1)
    expected_status: AgentRunStatus = Field(
        description="Status observed with expected_sequence; only queued/running runs can be cancelled."
    )
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("expected_status")
    @classmethod
    def validate_expected_status(cls, value: AgentRunStatus) -> AgentRunStatus:
        if value not in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}:
            raise ValueError("only queued or running runs can be cancelled")
        return value


class ApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_approval_sequence: int = Field(ge=1)
    expected_run_sequence: int = Field(ge=1)
    decision: str = Field(pattern="^(approve|deny)$")
    reason: str | None = Field(default=None, max_length=2000)


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    session_id: str | None
    requested_by_employee_id: str | None
    idempotency_key: str
    status: AgentRunStatus
    attempt: int = Field(ge=1)
    input_payload: dict[str, Any]
    output_payload: dict[str, Any] | None
    failure_code: str | None
    failure_message: str | None
    sequence: int = Field(ge=1)
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    tool_call_id: str
    approval_kind: str
    status: ApprovalRequestStatus
    requested_payload: dict[str, Any]
    decision_reason: str | None
    decided_by_employee_id: str | None
    requested_at: datetime
    expires_at: datetime | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalInboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval: ApprovalRecord
    project_id: str
    session_id: str | None
    run_status: AgentRunStatus
    approval_sequence: int = Field(ge=1)
    run_sequence: int = Field(ge=1)


class AgentRunList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AgentRunRecord]
    limit: int
    offset: int


class ApprovalInbox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ApprovalInboxItem]
    limit: int


class AgentRunDispatchReport(BaseModel):
    """Redacted outcome of the post-commit durable dispatch trigger.

    ``triggered`` says the capped consumer round actually ran; the counters are
    what it decided. A ``reason`` means delivery was deferred and the committed
    enqueue intent stays pending, claimable by the next consumer round.
    """

    model_config = ConfigDict(extra="forbid")

    triggered: bool
    claimed: int = Field(default=0, ge=0)
    delivered: int = Field(default=0, ge=0)
    retried: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    quarantined: int = Field(default=0, ge=0)
    lease_lost: int = Field(default=0, ge=0)
    reason: str | None = None


class AgentRunCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: AgentRunRecord
    duplicate: bool
    queue_intents_recorded: int = Field(ge=1)
    dispatch: AgentRunDispatchReport | None = None


class AgentRunCancelled(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: AgentRunRecord
    duplicate: bool
    queue_intents_recorded: int = Field(ge=1)
    dispatch: AgentRunDispatchReport | None = None


class ApprovalDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval: ApprovalRecord
    run: AgentRunRecord
    duplicate: bool
    queue_intents_recorded: int = Field(ge=2)
    dispatch: AgentRunDispatchReport | None = None


class EventTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    items: list[EventEnvelope]
    has_more: bool
    limit: int


class EventTimelineQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregate_type: str | None = None
    aggregate_id: str | None = Field(default=None, max_length=64)
    after_sequence: int | None = Field(default=None, ge=0)

    @field_validator("aggregate_type")
    @classmethod
    def validate_aggregate_type(cls, value: str | None) -> str | None:
        if value is not None and value not in {"agent_run", "approval_request"}:
            raise ValueError("aggregate_type must be agent_run or approval_request")
        return value

    def validate_cursor(self) -> None:
        if self.after_sequence is not None and (self.aggregate_type is None or self.aggregate_id is None):
            raise ValueError("after_sequence requires aggregate_type and aggregate_id")
        if (self.aggregate_type is None) != (self.aggregate_id is None):
            raise ValueError("aggregate_type and aggregate_id must be supplied together")


__all__ = [
    "AgentRunCancelled",
    "AgentRunCreated",
    "AgentRunDispatchReport",
    "AgentRunList",
    "AgentRunRecord",
    "ApprovalDecisionBody",
    "ApprovalDecisionResult",
    "ApprovalInbox",
    "ApprovalInboxItem",
    "ApprovalRecord",
    "CancelAgentRunBody",
    "CreateAgentRunBody",
    "EventTimeline",
    "EventTimelineQuery",
]
