"""Module registry — platform DB catalog of reusable meta templates."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _mod_id() -> str:
    return f"mod_{uuid.uuid4().hex[:16]}"


def _mcb_id() -> str:
    return f"mcb_{uuid.uuid4().hex[:16]}"


def _mpb_id() -> str:
    return f"mpb_{uuid.uuid4().hex[:16]}"


def _mcg_id() -> str:
    return f"mcg_{uuid.uuid4().hex[:16]}"


class ModuleRow(Base):
    __tablename__ = "modules"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_mod_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="platform", server_default="platform")
    owner_company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    company_grant_scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="selected",
        server_default="selected",
    )
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


class ModuleCompanyGrantRow(Base):
    __tablename__ = "module_company_grants"
    __table_args__ = (UniqueConstraint("module_id", "company_id", name="uq_module_company_grant"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_mcg_id)
    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    bind_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="local", server_default="local")
    child_may_edit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
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
    bind_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="local", server_default="local")
    child_may_edit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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
    bind_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="local", server_default="local")
    child_may_edit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _minst_id() -> str:
    return f"minst_{uuid.uuid4().hex[:16]}"


def _mimd_id() -> str:
    return f"mimd_{uuid.uuid4().hex[:16]}"


def _midr_id() -> str:
    return f"midr_{uuid.uuid4().hex[:16]}"


class ModuleInstanceRow(Base):
    """Independent fork of module meta+data owned by platform/company/cabinet/project."""

    __tablename__ = "module_instances"
    __table_args__ = (
        UniqueConstraint("owner_kind", "owner_id", "module_id", name="uq_module_instance_owner"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_minst_id)
    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("module_instances.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModuleInstanceMetaDocumentRow(Base):
    __tablename__ = "module_instance_meta_documents"
    __table_args__ = (
        UniqueConstraint("instance_id", "slug", name="uq_module_instance_meta_slug"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_mimd_id)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("module_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModuleInstanceDataRow(Base):
    __tablename__ = "module_instance_data_rows"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "table_slug",
            "row_id",
            name="uq_module_instance_data_row",
        ),
        Index(
            "ix_module_instance_data_rows_session",
            "instance_id",
            "table_slug",
            "session_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_midr_id)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("module_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    table_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    row_id: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
