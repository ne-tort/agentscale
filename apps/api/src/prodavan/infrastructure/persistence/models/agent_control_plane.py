"""Durable agent control-plane persistence (runs and approvals)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AgentRunRow(Base):
    """A durable execution attempt, independent from an agent session lifecycle."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_agent_runs_project_idempotency"),
        Index("ix_agent_runs_project_status_created", "project_id", "status", "created_at"),
        Index("ix_agent_runs_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("agr"))
    project_id: Mapped[str] = mapped_column(String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # A run can outlive a deleted/rotated session; it remains auditable.
    session_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_employee_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    output_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApprovalRequestRow(Base):
    """A durable human decision request belonging to exactly one agent run."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_approval_requests_run_tool_call"),
        Index("ix_approval_requests_status_expires", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("apr"))
    run_id: Mapped[str] = mapped_column(String(40), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    requested_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_employee_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
