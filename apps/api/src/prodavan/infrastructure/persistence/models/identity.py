"""ORM models — identity (Company / Employee / Membership)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class CompanyRow(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("co"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_lifetime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # DB ON DELETE CASCADE — do not ORM-NULL memberships.company_id (NOT NULL).
    memberships: Mapped[list[MembershipRow]] = relationship(
        back_populates="company",
        passive_deletes=True,
    )


class EmployeeRow(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("emp"))
    keycloak_sub: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="invited")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list[MembershipRow]] = relationship(
        back_populates="employee",
        passive_deletes=True,
    )


class MembershipRow(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("company_id", "employee_id", name="uq_membership_company_employee"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("mem"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped[CompanyRow] = relationship(back_populates="memberships")
    employee: Mapped[EmployeeRow] = relationship(back_populates="memberships")
