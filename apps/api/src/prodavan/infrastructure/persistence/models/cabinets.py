"""Cabinet instance registry — platform DB."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _cab_id() -> str:
    return f"cab_{uuid.uuid4().hex[:16]}"


def _grant_id() -> str:
    return f"ccg_{uuid.uuid4().hex[:16]}"


def _assignment_id() -> str:
    return f"cas_{uuid.uuid4().hex[:16]}"


class CabinetInstanceRow(Base):
    __tablename__ = "cabinet_instances"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_cab_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Legacy primary org anchor; mirrored from first active company grant when set.
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="platform", server_default="platform")
    owner_company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    base_template: Mapped[str] = mapped_column(String(64), nullable=False, default="base")
    company_grant_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="selected", server_default="selected"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CabinetCompanyGrantRow(Base):
    __tablename__ = "cabinet_company_grants"
    __table_args__ = (UniqueConstraint("cabinet_id", "company_id", name="uq_cabinet_company_grant"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_grant_id)
    cabinet_id: Mapped[str] = mapped_column(
        ForeignKey("cabinet_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="assigned_ro", server_default="assigned_ro")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CabinetEmployeeAssignmentRow(Base):
    __tablename__ = "cabinet_employee_assignments"
    __table_args__ = (UniqueConstraint("cabinet_id", "employee_id", name="uq_cabinet_employee_assignment"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_assignment_id)
    cabinet_id: Mapped[str] = mapped_column(
        ForeignKey("cabinet_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="operator", server_default="operator")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
