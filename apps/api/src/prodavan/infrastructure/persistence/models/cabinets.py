"""Cabinet instance registry — platform DB."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _cab_id() -> str:
    return f"cab_{uuid.uuid4().hex[:16]}"


class CabinetInstanceRow(Base):
    __tablename__ = "cabinet_instances"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_cab_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Nullable: Admin-created cabinets have no employee owner until assignment exists.
    owner_employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    base_template: Mapped[str] = mapped_column(String(64), nullable=False, default="base")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
