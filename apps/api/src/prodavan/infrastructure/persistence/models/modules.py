"""Module registry — platform DB catalog of reusable meta templates."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _mod_id() -> str:
    return f"mod_{uuid.uuid4().hex[:16]}"


def _mcb_id() -> str:
    return f"mcb_{uuid.uuid4().hex[:16]}"


def _mpb_id() -> str:
    return f"mpb_{uuid.uuid4().hex[:16]}"


class ModuleRow(Base):
    __tablename__ = "modules"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_mod_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModuleMetaDocumentRow(Base):
    __tablename__ = "module_meta_documents"
    __table_args__ = (UniqueConstraint("module_id", "slug", name="uq_module_meta_document_slug"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: f"mmd_{uuid.uuid4().hex[:16]}")
    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModuleCabinetBindingRow(Base):
    __tablename__ = "module_cabinet_bindings"
    __table_args__ = (UniqueConstraint("module_id", "cabinet_id", name="uq_module_cabinet_binding"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_mcb_id)
    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), nullable=False)
    cabinet_id: Mapped[str] = mapped_column(
        ForeignKey("cabinet_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModuleProjectBindingRow(Base):
    __tablename__ = "module_project_bindings"
    __table_args__ = (UniqueConstraint("module_id", "project_id", name="uq_module_project_binding"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_mpb_id)
    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
