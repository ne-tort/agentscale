"""Durable, secret-free projection of plugin route registrations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


class PluginRouteRow(Base):
    """One route projection per exact company/plugin/grant binding.

    The FK is deliberately RESTRICT: uninstall unregisters this row before it
    deletes the grant. No egress allowlist, DSN, token or secret reference is
    copied into this table; only the derived rule count is persisted.
    """

    __tablename__ = "plugin_routes"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "plugin_id",
            "grant_id",
            name="uq_plugin_route_company_plugin_grant",
        ),
        ForeignKeyConstraint(
            ["grant_id"],
            ["plugin_capability_grants.id"],
            name="fk_plugin_route_grant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "egress_rule_count >= 0",
            name="ck_plugin_route_egress_rule_count",
        ),
    )

    company_id: Mapped[str] = mapped_column(String(40), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(160), nullable=False)
    grant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(80), nullable=False)
    webhook_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    pluginpage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    capability_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    egress_rule_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    deployment: Mapped[str] = mapped_column(String(63), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["PluginRouteRow"]
