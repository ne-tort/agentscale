"""SQL persistence for source-backed installation-parent scope."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.domain.errors import AppError
from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.installation_scope import (
    PluginInstallationScopeRow,
)
from prodavan.infrastructure.persistence.models.plugin_platform import ModuleSourceRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

from .contracts import InstallationScope


class SqlInstallationScopeRepository:
    """Read and idempotently bind exact source-backed installation scope."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        if (session is None) == (session_factory is None):
            raise ValueError("provide exactly one AsyncSession or async_sessionmaker")
        self._session = session
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session_ctx(self) -> AsyncIterator[AsyncSession]:
        if self._session is not None:
            yield self._session
            return
        assert self._session_factory is not None
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @staticmethod
    def _unavailable(detail: str) -> AppError:
        return AppError(
            code="PLUGIN_INSTALLATION_SCOPE_UNAVAILABLE",
            title="Plugin installation scope unavailable",
            status=503,
            detail=detail,
        )

    @staticmethod
    def _mismatch(detail: str) -> AppError:
        return AppError(
            code="PLUGIN_BOOTSTRAP_SCOPE_MISMATCH",
            title="Plugin installation scope mismatch",
            status=503,
            detail=detail,
        )

    @staticmethod
    def _to_domain(row: PluginInstallationScopeRow, project: ProjectRow) -> InstallationScope:
        if (
            project.status != "active"
            or row.company_id != project.company_id
            or row.cabinet_id != project.cabinet_id
            or row.project_id != project.id
        ):
            raise SqlInstallationScopeRepository._mismatch(
                "durable installation scope does not match ProjectRow tenant anchors"
            )
        return InstallationScope(
            company_id=row.company_id,
            plugin_instance_id=row.plugin_instance_id,
            cabinet_id=row.cabinet_id,
            project_id=row.project_id,
        )

    async def get(
        self, *, company_id: str, plugin_instance_id: str
    ) -> InstallationScope | None:
        async with self._session_ctx() as session:
            row = await session.get(PluginInstallationScopeRow, plugin_instance_id)
            if row is None:
                return None
            if row.company_id != company_id:
                raise self._mismatch(
                    "durable installation scope tenant does not match the installation"
                )
            project = await session.get(ProjectRow, row.project_id)
            if project is None:
                raise self._mismatch(
                    "durable installation scope ProjectRow is missing"
                )
            return self._to_domain(row, project)

    async def ensure_from_source(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str
    ) -> InstallationScope:
        """Create once from a READY ModuleSource, then preserve stable identity.

        No legacy/random/ambiguous instance is backfilled: a new binding requires
        the deterministic source instance and exact ProjectRow tenant anchors.
        """

        async with self._session_ctx() as session:
            existing = await session.get(PluginInstallationScopeRow, plugin_instance_id)
            if existing is not None:
                if existing.company_id != company_id:
                    raise self._mismatch(
                        "durable installation scope belongs to another tenant"
                    )
                project = await session.get(ProjectRow, existing.project_id)
                if project is None:
                    raise self._mismatch(
                        "durable installation scope ProjectRow is missing"
                    )
                return self._to_domain(existing, project)

            source = (
                await session.execute(
                    select(ModuleSourceRow).where(
                        ModuleSourceRow.company_id == company_id,
                        ModuleSourceRow.plugin_id == plugin_id,
                    )
                )
            ).scalar_one_or_none()
            if source is None or source.status != "ready":
                raise self._unavailable(
                    "a READY ModuleSource is required for installation scope binding"
                )
            expected_instance = plugin_instance_id_for(company_id, plugin_id)
            if plugin_instance_id != expected_instance:
                raise self._unavailable(
                    "legacy or non-deterministic installation ids are not scope-backfillable"
                )
            cabinet_id = source.cabinet_id
            project_id = source.project_id
            if not cabinet_id or not project_id:
                raise self._unavailable(
                    "ModuleSource has no authoritative cabinet_id/project_id binding"
                )
            project = await session.get(ProjectRow, project_id)
            if (
                project is None
                or project.status != "active"
                or project.company_id != company_id
                or project.cabinet_id != cabinet_id
            ):
                raise self._mismatch(
                    "ModuleSource scope does not match ProjectRow tenant anchors"
                )
            company = await session.get(CompanyRow, company_id)
            if company is None or company.deleted_at is not None or company.status != "active":
                raise self._mismatch(
                    "ModuleSource scope requires an active tenant company"
                )
            row = PluginInstallationScopeRow(
                plugin_instance_id=plugin_instance_id,
                company_id=company_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
            )
            session.add(row)
            await session.flush()
            return InstallationScope(
                company_id=company_id,
                plugin_instance_id=plugin_instance_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
            )


__all__ = ["SqlInstallationScopeRepository"]
