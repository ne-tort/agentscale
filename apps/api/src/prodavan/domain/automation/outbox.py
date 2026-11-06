"""Domain contracts for a transactionally durable automation outbox.

The event is immutable business data.  Lifecycle, leases, and retry scheduling
are represented by :class:`OutboxState` so persistence adapters can update state
without mutating the event payload.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class OutboxState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class OutboxEvent(_Contract):
    """An aggregate event whose publication is durable and idempotent."""

    event_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=64)
    aggregate_type: str = Field(min_length=1, max_length=128)
    aggregate_id: str = Field(min_length=1, max_length=128)
    aggregate_version: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=200)
    payload: Mapping[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def default_and_validate_key(self) -> OutboxEvent:
        expected = self.aggregate_event_key(
            tenant_id=self.tenant_id,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            aggregate_version=self.aggregate_version,
        )
        if self.idempotency_key is not None and self.idempotency_key != expected:
            raise ValueError("idempotency_key must match aggregate identity and version")
        if self.idempotency_key is None:
            object.__setattr__(self, "idempotency_key", expected)
        return self

    @staticmethod
    def aggregate_event_key(
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
    ) -> str:
        """Build a stable, injective key for one tenant aggregate version."""
        parts = (tenant_id.strip(), aggregate_type.strip(), aggregate_id.strip(), str(aggregate_version))
        if not all(parts[:3]) or aggregate_version < 1:
            raise ValueError("tenant and aggregate identity/version must be valid")
        return ":".join(f"{len(part)}={part}" for part in parts)


class OutboxStateRecord(_Contract):
    """Durable delivery state associated with one :class:`OutboxEvent`."""

    event_id: str = Field(min_length=1, max_length=128)
    state: OutboxState = OutboxState.PENDING
    attempts: int = Field(default=0, ge=0)
    available_at: datetime
    leased_by: str | None = Field(default=None, min_length=1, max_length=128)
    lease_until: datetime | None = None
    last_error: str | None = Field(default=None, max_length=2_000)
    delivered_at: datetime | None = None
    dead_lettered_at: datetime | None = None

    @field_validator("available_at", "lease_until", "delivered_at", "dead_lettered_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("outbox timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_transition_shape(self) -> OutboxStateRecord:
        if self.state is OutboxState.LEASED:
            if not self.leased_by or self.lease_until is None:
                raise ValueError("leased outbox state requires owner and lease_until")
        elif self.leased_by is not None or self.lease_until is not None:
            raise ValueError("only leased outbox state may carry a lease")

        if self.state is OutboxState.RETRY_WAIT and self.last_error is None:
            raise ValueError("retry_wait outbox state requires last_error")
        if self.state is OutboxState.DELIVERED and self.delivered_at is None:
            raise ValueError("delivered outbox state requires delivered_at")
        if self.state is OutboxState.DEAD_LETTER and self.dead_lettered_at is None:
            raise ValueError("dead_letter outbox state requires dead_lettered_at")
        return self


class OutboxEnqueueIntent(_Contract):
    """The broker/queue intent committed in the same transaction as the event."""

    event_id: str = Field(min_length=1, max_length=128)
    queue_name: str = Field(min_length=1, max_length=200)
    deduplication_key: str = Field(min_length=1, max_length=512)


__all__ = ["OutboxEnqueueIntent", "OutboxEvent", "OutboxState", "OutboxStateRecord"]
