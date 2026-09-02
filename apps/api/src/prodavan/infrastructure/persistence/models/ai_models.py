"""AI model catalog + SDK/key bindings (L03/L08)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AiModelRow(Base):
    __tablename__ = "ai_models"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("mdl"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="platform", server_default="platform")
    owner_company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiModelSdkBindingRow(Base):
    __tablename__ = "ai_model_sdk_bindings"
    __table_args__ = (UniqueConstraint("model_id", "api_kind", name="uq_ai_model_sdk"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("msb"))
    model_id: Mapped[str] = mapped_column(ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False)
    api_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiKeyModelBindingRow(Base):
    __tablename__ = "ai_key_model_bindings"
    __table_args__ = (UniqueConstraint("key_id", "model_id", name="uq_ai_key_model"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("kmb"))
    key_id: Mapped[str] = mapped_column(ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False)
    model_id: Mapped[str] = mapped_column(ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
