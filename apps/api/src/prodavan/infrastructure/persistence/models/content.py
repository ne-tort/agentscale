"""ORM — Content Service (assets, aliases, ACL)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class ContentAssetRow(Base):
    __tablename__ = "content_assets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("cast"))
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="company")
    owner_company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="company")
    mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(String(260), nullable=True)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContentBlobVersionRow(Base):
    __tablename__ = "content_blob_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("cbv"))
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("content_assets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    object_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("asset_id", "version", name="uq_content_blob_asset_version"),)


class ContentAliasRow(Base):
    __tablename__ = "content_aliases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("cal"))
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    owner_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="company")
    owner_company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="company")
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContentAliasBindingRow(Base):
    __tablename__ = "content_alias_bindings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("cab"))
    alias_id: Mapped[str] = mapped_column(
        ForeignKey("content_aliases.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("content_assets.id", ondelete="CASCADE"), nullable=False
    )
    blob_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_blob_versions.id", ondelete="SET NULL"), nullable=True
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bound_by: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )


class ContentAclEntryRow(Base):
    __tablename__ = "content_acl_entries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("acl"))
    resource_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(40), nullable=False)
    principal_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(40), nullable=False)
    permission: Mapped[str] = mapped_column(String(16), nullable=False, default="read")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "resource_kind",
            "resource_id",
            "principal_kind",
            "principal_id",
            "permission",
            name="uq_content_acl_grant",
        ),
    )


class ContentAssetLinkRow(Base):
    __tablename__ = "content_asset_links"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("lnk"))
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("content_assets.id", ondelete="CASCADE"), nullable=False
    )
    link_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    link_id: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("link_kind", "link_id", name="uq_content_asset_link_target"),
    )
