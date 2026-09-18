"""AI Provider Keys — ORM (L03)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AiProviderKeyRow(Base):
    __tablename__ = "ai_provider_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("aik"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    api_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # platform = Admin-owned (bind via company_ai_key_bindings); company = owned by owner_company_id
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="platform", server_default="platform")
    owner_company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
    )
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    next_renewal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewal_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CompanyAiKeyBindingRow(Base):
    __tablename__ = "company_ai_key_bindings"
    __table_args__ = (UniqueConstraint("company_id", "key_id", name="uq_company_ai_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("ckb"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[str] = mapped_column(ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeeAiKeyBindingRow(Base):
    __tablename__ = "employee_ai_key_bindings"
    __table_args__ = (
        UniqueConstraint("company_id", "employee_id", "key_id", name="uq_employee_ai_key_company"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("ekb"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[str] = mapped_column(ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectAiKeyBindingRow(Base):
    __tablename__ = "project_ai_key_bindings"
    __table_args__ = (UniqueConstraint("project_id", "key_id", name="uq_project_ai_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("pkb"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[str] = mapped_column(ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabinetAiKeyBindingRow(Base):
    __tablename__ = "cabinet_ai_key_bindings"
    __table_args__ = (UniqueConstraint("cabinet_id", "key_id", name="uq_cabinet_ai_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("cab"))
    cabinet_id: Mapped[str] = mapped_column(
        ForeignKey("cabinet_instances.id", ondelete="CASCADE"), nullable=False
    )
    key_id: Mapped[str] = mapped_column(ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiKeyAuditEventRow(Base):
    """Mutation audit for AI keys (was created ad-hoc via SQL; must live in ORM for alembic check)."""

    __tablename__ = "ai_key_audit_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: _id("aae"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiKeyCheckResultRow(Base):
    """Last probe result for an AI key (one row per key, upserted).

    Stores the latest verification data: status, latency, models list, error.
    Kept separate from ai_provider_keys to avoid mixing mutable probe state
    with the key record. The probe never mutates the key itself.
    """

    __tablename__ = "ai_key_check_results"
    # 1:1 with ai_provider_keys — key_id is the PK so upsert is a single write.
    key_id: Mapped[str] = mapped_column(
        ForeignKey("ai_provider_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Numeric(10, 0), nullable=True)
    models: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    default_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Numeric(6, 0), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    checked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
