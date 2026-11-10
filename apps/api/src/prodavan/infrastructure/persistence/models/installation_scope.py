"""Durable trusted installation-parent scope for private plugin bootstrap."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


class PluginInstallationScopeRow(Base):
    """Exact authoritative parent binding for one durable plugin installation.

    Rows are written only by the trusted ModuleSource/admin scope flow. There is
    deliberately no migration backfill; missing rows keep private bootstrap
    fail-closed and public responses company-only.
    """

    __tablename__ = "plugin_installation_scopes"
    __table_args__ = (
        # Composite FKs make tenant/cabinet/project and tenant/instance
        # consistency explicit at the database boundary.
        ForeignKeyConstraint(
            ["project_id", "company_id", "cabinet_id"],
            ["projects.id", "projects.company_id", "projects.cabinet_id"],
            name="fk_plugin_installation_scope_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "plugin_instance_id"],
            ["plugin_instances.company_id", "plugin_instances.id"],
            name="fk_plugin_installation_scope_instance",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_plugin_installation_scope_company",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "company_id <> '' AND company_id = btrim(company_id)",
            name="ck_plugin_installation_scope_company_nonempty",
        ),
        CheckConstraint(
            "cabinet_id <> '' AND cabinet_id = btrim(cabinet_id)",
            name="ck_plugin_installation_scope_cabinet_nonempty",
        ),
        CheckConstraint(
            "project_id <> '' AND project_id = btrim(project_id)",
            name="ck_plugin_installation_scope_project_nonempty",
        ),
    )

    #: Exact durable plugin installation id. One binding per installation only.
    plugin_instance_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cabinet_id: Mapped[str] = mapped_column(String(40), nullable=False)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["PluginInstallationScopeRow"]
