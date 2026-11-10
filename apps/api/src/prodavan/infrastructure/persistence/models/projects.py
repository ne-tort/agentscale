"""ORM — projects runtime (L07)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint(
            "id", "company_id", "cabinet_id", name="uq_project_tenant_cabinet_anchor"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    cabinet_id: Mapped[str] = mapped_column(ForeignKey("cabinet_instances.id", ondelete="CASCADE"), nullable=False)
    owner_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    visibility_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="cabinet_shared")
    workspace_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    container_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_ai_key_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_provider_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    materialize_manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    launch_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resume_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    budget_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Chat reconnect policy for provider errors (migration 2026100202):
    # {"interval_sec": int, "max_attempts": int (0=∞), "fallback_models": [str]}.
    # NULL = server default (10s / unlimited / no fallbacks).
    chat_error_policy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    workspace_outdated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # WAVE16: sync отложен из-за активного хода агента (rematerialize при
    # turn-in-progress). Снимается и исполняется при окончании хода
    # (_emit_webhook_on_turn_end) или sweep'ом по таймауту.
    workspace_sync_pending_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    workspace_sync_pending_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectPodRow(Base):
    __tablename__ = "project_pods"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    workspace_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    desired_state: Mapped[str] = mapped_column(String(32), nullable=False, default="absent")
    runtime_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hydrate_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectEmployeeAssignmentRow(Base):
    __tablename__ = "project_employee_assignments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("pea"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    cabinet_id: Mapped[str] = mapped_column(
        ForeignKey("cabinet_instances.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectScheduleRow(Base):
    __tablename__ = "project_schedules"
    __table_args__ = (
        Index("ix_project_schedules_due", "enabled", "next_run_at"),
        Index("ix_project_schedules_project", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("sch"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectTriggerRow(Base):
    __tablename__ = "project_triggers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("trg"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectAttachmentRow(Base):
    __tablename__ = "project_attachments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("att"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_assets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
