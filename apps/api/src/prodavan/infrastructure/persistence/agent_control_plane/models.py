"""ORM rows owned by the durable agent control-plane adapter."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class AgentControlPlaneRunScopeRow(Base):
    """Immutable tenant/project/session ownership for a control-plane run."""

    __tablename__ = "agent_control_plane_run_scopes"
    __table_args__ = (
        Index("ix_acp_run_scope_tenant_project", "tenant_id", "project_id", "run_id"),
    )

    run_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentControlPlaneAggregateRow(Base):
    """CAS cursor for one run or approval aggregate."""

    __tablename__ = "agent_control_plane_aggregates"

    aggregate_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentControlPlaneEventRow(Base):
    """Append-only ordered domain event with immutable scope and actor evidence."""

    __tablename__ = "agent_control_plane_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type", "aggregate_id", "sequence",
            name="uq_acp_events_aggregate_sequence",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_acp_events_tenant_idempotency"),
        Index("ix_acp_events_run_sequence", "run_id", "sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(160), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    producer: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentControlPlaneOutboxEventRow(Base):
    """Durable outbox projection written atomically with its domain event."""

    __tablename__ = "agent_control_plane_outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_acp_outbox_idempotency"),
        UniqueConstraint(
            "tenant_id", "aggregate_type", "aggregate_id", "aggregate_version",
            name="uq_acp_outbox_aggregate_version",
        ),
        Index("ix_acp_outbox_pending", "created_at", "event_id"),
    )

    event_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agent_control_plane_events.event_id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentControlPlaneEnqueueIntentRow(Base):
    """Pending queue work; dispatch is intentionally owned by separate host wiring."""

    __tablename__ = "agent_control_plane_enqueue_intents"
    __table_args__ = (
        UniqueConstraint("queue_name", "deduplication_key", name="uq_acp_queue_deduplication"),
        Index("ix_acp_enqueue_due", "queue_name", "state", "available_at", "lease_until"),
    )

    event_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agent_control_plane_outbox_events.event_id", ondelete="CASCADE"), primary_key=True
    )
    queue_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentControlPlaneCommandRow(Base):
    """Durable command idempotency receipt, inserted with the domain transaction."""

    __tablename__ = "agent_control_plane_commands"
    __table_args__ = (
        Index("ix_acp_commands_tenant_project", "tenant_id", "project_id", "created_at"),
    )

    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    command_idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")
    result_payload: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AgentControlPlaneAggregateRow",
    "AgentControlPlaneCommandRow",
    "AgentControlPlaneEnqueueIntentRow",
    "AgentControlPlaneEventRow",
    "AgentControlPlaneOutboxEventRow",
    "AgentControlPlaneRunScopeRow",
]
