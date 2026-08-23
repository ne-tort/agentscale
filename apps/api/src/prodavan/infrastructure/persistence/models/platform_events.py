"""Platform lifecycle events bus (L07) — separate from project_triggers."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id() -> str:
    return f"pev_{uuid.uuid4().hex[:16]}"


class PlatformEventRow(Base):
    __tablename__ = "platform_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    company_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_sub: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
