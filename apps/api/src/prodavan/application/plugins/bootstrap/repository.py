"""SQLAlchemy read model for company-facing plugin bootstrap.

There is deliberately no in-memory production fallback. The active company,
installation, artifact and enabled flag are read from their durable rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from prodavan.application.plugins.installation_scope.contracts import InstallationScope
from prodavan.application.plugins.installation_scope.repository import (
    SqlInstallationScopeRepository,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.plugin_platform import (
    CapabilityGrantRow,
    PluginArtifactRow,
    PluginFeatureFlagRow,
    PluginInstanceRow,
)


@dataclass(frozen=True, slots=True)
class InstallationRows:
    grant: CapabilityGrantRow
    instance: PluginInstanceRow
    artifact: PluginArtifactRow
    enabled: bool
    # Absent means no authoritative durable parent binding, not tenant-wide access.
    scope: InstallationScope | None = None


class PluginBootstrapRepository(Protocol):
    async def active_company(self, company_id: str) -> CompanyRow | None: ...
    async def require_membership(self, employee: EmployeeRow, company_id: str) -> CompanyRow: ...
    async def installation(self, company_id: str, plugin_id: str) -> InstallationRows | None: ...
    async def installation_scope(self, rows: InstallationRows) -> InstallationScope | None: ...


class SqlAlchemyPluginBootstrapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_company(self, company_id: str) -> CompanyRow | None:
        result = await self._session.execute(
            select(CompanyRow).where(
                CompanyRow.id == company_id,
                CompanyRow.deleted_at.is_(None),
                CompanyRow.status == "active",
            )
        )
        return result.scalars().first()

    async def require_membership(self, employee: EmployeeRow, company_id: str) -> CompanyRow:
        result = await self._session.execute(
            select(CompanyRow)
            .join(MembershipRow, MembershipRow.company_id == CompanyRow.id)
            .where(
                MembershipRow.employee_id == employee.id,
                CompanyRow.id == company_id,
                CompanyRow.deleted_at.is_(None),
                CompanyRow.status == "active",
            )
        )
        company = result.scalars().first()
        if company is None:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="active company membership required",
            )
        return company

    async def installation(self, company_id: str, plugin_id: str) -> InstallationRows | None:
        grant = (
            await self._session.execute(
                select(CapabilityGrantRow)
                .where(
                    CapabilityGrantRow.company_id == company_id,
                    CapabilityGrantRow.plugin_id == plugin_id,
                )
                .order_by(CapabilityGrantRow.issued_at.desc())
            )
        ).scalars().first()
        if grant is None:
            return None
        instance = await self._session.get(PluginInstanceRow, grant.plugin_instance_id)
        artifact = (
            await self._session.execute(
                select(PluginArtifactRow).where(
                    PluginArtifactRow.plugin_id == grant.plugin_id,
                    PluginArtifactRow.version == grant.manifest_version,
                    PluginArtifactRow.oci_digest == grant.artifact_digest,
                )
            )
        ).scalars().first()
        flag = await self._session.get(PluginFeatureFlagRow, company_id)
        enabled = bool(flag and isinstance(flag.enabled_modules, list) and plugin_id in flag.enabled_modules)
        # Scope is loaded separately after service-side exact grant+instance
        # validation. Legacy module bindings are never consulted.
        if instance is None or artifact is None:
            return InstallationRows(grant=grant, instance=instance, artifact=artifact, enabled=enabled)  # type: ignore[arg-type]
        return InstallationRows(grant=grant, instance=instance, artifact=artifact, enabled=enabled)

    async def installation_scope(self, rows: InstallationRows) -> InstallationScope | None:
        """Read durable scope only after exact grant+instance validation."""
        return await SqlInstallationScopeRepository(self._session).get(
            company_id=rows.grant.company_id,
            plugin_instance_id=rows.grant.plugin_instance_id,
        )

__all__ = [
    "InstallationRows",
    "InstallationScope",
    "PluginBootstrapRepository",
    "SqlAlchemyPluginBootstrapRepository",
]
