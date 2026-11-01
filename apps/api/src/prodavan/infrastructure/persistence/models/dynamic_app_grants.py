"""Persistence model for explicit dynamic-app capability grants.

This table is append-oriented: revocation is represented on the grant row so
an authorization decision remains auditable instead of deleting history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


_SCOPE_CHECK = "('tenant', 'cabinet', 'project')"


def _id() -> str:
    return f"dgrant_{uuid.uuid4().hex[:16]}"


class DynamicAppCapabilityGrantRow(Base):
    """One explicit grant for one app version and one tenant scope."""

    __tablename__ = "dynamic_app_capability_grants"
    __table_args__ = (
        CheckConstraint(f"scope_kind IN {_SCOPE_CHECK}", name="ck_dynamic_app_cap_grant_scope_kind"),
        CheckConstraint("jsonb_typeof(operations) = 'array' AND jsonb_array_length(operations) > 0", name="ck_dynamic_app_cap_grant_operations_nonempty"),
        CheckConstraint("jsonb_typeof(resources) = 'array' AND jsonb_array_length(resources) > 0", name="ck_dynamic_app_cap_grant_resources_nonempty"),
        CheckConstraint("jsonb_typeof(domains) = 'array' AND jsonb_array_length(domains) > 0", name="ck_dynamic_app_cap_grant_domains_nonempty"),
        CheckConstraint("expires_at IS NULL OR expires_at > issued_at", name="ck_dynamic_app_cap_grant_expiry_after_issue"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= issued_at", name="ck_dynamic_app_cap_grant_revoke_after_issue"),
        CheckConstraint("revoked_at IS NULL OR revoked_by IS NOT NULL", name="ck_dynamic_app_cap_grant_revoke_actor"),
        Index("ix_dynamic_app_cap_grants_lookup", "tenant_id", "scope_kind", "scope_id", "app_id", "app_version_id"),
        Index("ix_dynamic_app_cap_grants_validity", "issued_at", "expires_at", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=_id)
    app_id: Mapped[str] = mapped_column(String(160), nullable=False)
    app_version_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("dynamic_app_versions.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    operations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    resources: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[str] = mapped_column(String(80), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = ["DynamicAppCapabilityGrantRow"]
