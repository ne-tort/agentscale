"""Typed entities for the automation/webhook control plane.

The module is deliberately persistence- and transport-neutral. It describes the
state that an API, queue worker, or future persistence adapter must exchange.
Secrets are represented by references only; payloads remain JSON-like mappings.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AutomationStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class TaskRunState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_RETRY = "waiting_retry"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    DELIVERED = "delivered"
    RETRY_WAIT = "retry_wait"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


class IdempotencyState(StrEnum):
    RESERVED = "reserved"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class LeaseState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class _Timestamped(_Contract):
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_update_order(self) -> _Timestamped:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class RetryPolicy(_Contract):
    max_attempts: int = Field(default=5, ge=1, le=100)
    initial_delay_seconds: int = Field(default=60, ge=0)
    max_delay_seconds: int = Field(default=7_200, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=100.0)

    @model_validator(mode="after")
    def validate_delay_bounds(self) -> RetryPolicy:
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds")
        return self


class AutomationDefinition(_Timestamped):
    """Versioned orchestration definition triggered by an inbound event."""

    id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    status: AutomationStatus = AutomationStatus.DRAFT
    trigger_type: str = Field(min_length=1, max_length=128)
    trigger_config: Mapping[str, Any] = Field(default_factory=dict)
    action_type: str = Field(min_length=1, max_length=128)
    action_config: Mapping[str, Any] = Field(default_factory=dict)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


class InboundWebhookEndpoint(_Timestamped):
    """Public webhook address metadata; the signing secret is never embedded."""

    id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    automation_id: str = Field(min_length=1, max_length=64)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    secret_ref: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    accepted_methods: tuple[str, ...] = ("POST",)
    max_body_bytes: int = Field(default=1_048_576, gt=0)

    @field_validator("accepted_methods")
    @classmethod
    def normalize_methods(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        methods = tuple(method.upper() for method in value)
        if not methods:
            raise ValueError("accepted_methods must not be empty")
        if any(not method.isalpha() for method in methods):
            raise ValueError("accepted_methods must contain HTTP method names")
        return methods


class TaskRun(_Timestamped):
    """A durable execution, independent from an agent conversation session."""

    id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    automation_id: str = Field(min_length=1, max_length=64)
    source_event_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=200)
    state: TaskRunState = TaskRunState.QUEUED
    attempt: int = Field(default=0, ge=0)
    input_payload: Mapping[str, Any] = Field(default_factory=dict)
    output_payload: Mapping[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2_000)
    next_retry_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lease_until: datetime | None = None
    leased_by: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("next_retry_at", "started_at", "finished_at", "lease_until")
    @classmethod
    def optional_timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> TaskRun:
        terminal = {TaskRunState.SUCCEEDED, TaskRunState.FAILED, TaskRunState.CANCELLED}
        if self.state in terminal and self.finished_at is None:
            raise ValueError("terminal task run requires finished_at")
        if self.state is TaskRunState.WAITING_RETRY and self.next_retry_at is None:
            raise ValueError("waiting_retry task run requires next_retry_at")
        if self.state is TaskRunState.FAILED and not self.error_message:
            raise ValueError("failed task run requires error_message")
        leased = {TaskRunState.LEASED, TaskRunState.RUNNING}
        if self.state in leased and (self.lease_until is None or self.leased_by is None):
            raise ValueError("leased task run requires an active lease")
        if self.state not in leased and (self.lease_until is not None or self.leased_by is not None):
            raise ValueError("only leased task runs may carry a lease")
        if self.finished_at is not None and self.started_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class IdempotencyRecord(_Timestamped):
    """Claim for a client key, allowing duplicate requests to replay one result."""

    key: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=128)
    state: IdempotencyState = IdempotencyState.RESERVED
    request_hash: str = Field(min_length=1, max_length=128)
    task_run_id: str = Field(min_length=1, max_length=64)
    response_payload: Mapping[str, Any] | None = None
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expiry_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class DeliveryAttempt(_Contract):
    attempt: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime | None = None
    response_status: int | None = Field(default=None, ge=100, le=599)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2_000)

    @field_validator("started_at", "finished_at")
    @classmethod
    def attempt_timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_attempt_order(self) -> DeliveryAttempt:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class OutboundDelivery(_Timestamped):
    """One independently retryable delivery of an automation event."""

    id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    task_run_id: str = Field(min_length=1, max_length=64)
    destination_url: str = Field(min_length=1, max_length=2_048)
    state: DeliveryState = DeliveryState.QUEUED
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1, le=100)
    payload: Mapping[str, Any] = Field(default_factory=dict)
    headers: Mapping[str, str] = Field(default_factory=dict)
    next_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    lease_until: datetime | None = None
    leased_by: str | None = Field(default=None, min_length=1, max_length=128)
    last_status: int | None = Field(default=None, ge=100, le=599)
    last_error: str | None = Field(default=None, max_length=2_000)
    attempts: tuple[DeliveryAttempt, ...] = ()

    @field_validator("next_attempt_at", "delivered_at", "lease_until")
    @classmethod
    def delivery_timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_delivery(self) -> OutboundDelivery:
        terminal = {DeliveryState.DELIVERED, DeliveryState.EXHAUSTED, DeliveryState.CANCELLED}
        if self.state is DeliveryState.RETRY_WAIT and self.next_attempt_at is None:
            raise ValueError("retry_wait delivery requires next_attempt_at")
        if self.state is DeliveryState.DELIVERED and self.delivered_at is None:
            raise ValueError("delivered delivery requires delivered_at")
        if self.state is DeliveryState.LEASED and (self.lease_until is None or self.leased_by is None):
            raise ValueError("leased delivery requires an active lease")
        if self.state is not DeliveryState.LEASED and (self.lease_until is not None or self.leased_by is not None):
            raise ValueError("only leased deliveries may carry a lease")
        if self.state in terminal and self.state is DeliveryState.EXHAUSTED and self.attempt < self.max_attempts:
            raise ValueError("exhausted delivery must use all attempts")
        if len(self.attempts) > self.attempt:
            raise ValueError("attempt history cannot exceed attempt counter")
        return self


class DeliveryLease(_Contract):
    """Worker ownership lease for a delivery or task run."""

    resource_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=128)
    state: LeaseState = LeaseState.ACTIVE
    acquired_at: datetime
    expires_at: datetime
    released_at: datetime | None = None

    @field_validator("acquired_at", "expires_at", "released_at")
    @classmethod
    def lease_timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("lease timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lease(self) -> DeliveryLease:
        if self.expires_at <= self.acquired_at:
            raise ValueError("expires_at must be after acquired_at")
        if self.state is LeaseState.RELEASED and self.released_at is None:
            raise ValueError("released lease requires released_at")
        if self.released_at is not None and self.released_at < self.acquired_at:
            raise ValueError("released_at must not precede acquired_at")
        return self


__all__ = [
    "AutomationDefinition",
    "AutomationStatus",
    "DeliveryAttempt",
    "DeliveryLease",
    "DeliveryState",
    "IdempotencyRecord",
    "IdempotencyState",
    "InboundWebhookEndpoint",
    "LeaseState",
    "OutboundDelivery",
    "RetryPolicy",
    "TaskRun",
    "TaskRunState",
]
