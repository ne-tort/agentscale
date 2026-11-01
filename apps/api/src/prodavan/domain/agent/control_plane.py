"""Durable agent control-plane contracts.

These contracts intentionally model a run as an execution record, not as a
session. A session is conversation state; a run is retryable orchestration
state and may reference a session without owning its lifecycle.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class EventEnvelope(BaseModel):
    """Versioned, idempotent event contract for durable control-plane work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str = Field(min_length=1, max_length=128)
    schema_version: int = Field(default=1, ge=1)
    occurred_at: datetime
    producer: str = Field(min_length=1, max_length=128)
    aggregate_type: Literal["agent_run", "approval_request", "agent_session"]
    aggregate_id: str = Field(min_length=1, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    sequence: int | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    requested_by_employee_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    input_payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunView(AgentRunCreate):
    id: str = Field(min_length=1, max_length=64)
    status: AgentRunStatus
    attempt: int = Field(default=1, ge=1)
    output_payload: dict[str, Any] | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64)
    tool_call_id: str = Field(min_length=1, max_length=128)
    approval_kind: str = Field(min_length=1, max_length=64)
    requested_payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[ApprovalRequestStatus.APPROVED, ApprovalRequestStatus.DENIED]
    decided_by_employee_id: str = Field(min_length=1, max_length=64)
    decision_reason: str | None = Field(default=None, max_length=2000)


class ApprovalRequestView(ApprovalRequestCreate):
    id: str = Field(min_length=1, max_length=64)
    status: ApprovalRequestStatus
    decision_reason: str | None = None
    decided_by_employee_id: str | None = None
    requested_at: datetime
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
