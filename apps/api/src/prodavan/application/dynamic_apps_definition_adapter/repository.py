"""SQLAlchemy read adapter for the M04.e definition/version repository port.

This module owns only ORM-to-domain mapping and scoped reads.  It never writes
registry rows and deliberately lets malformed persisted rows surface as an
application-level quarantine outcome through ``resolve``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.dynamic_apps_definition.contracts import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
)
from prodavan.application.dynamic_apps_definition.ports import DefinitionVersionRepository
from prodavan.application.dynamic_apps_definition.service import DynamicAppDefinitionService
from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
    RequestedCapability,
    RollbackMetadata,
)
from prodavan.infrastructure.persistence.models.dynamic_apps import AppDefinitionRow, AppVersionRow


class DynamicAppDefinitionRepositoryAdapter(DefinitionVersionRepository):
    """Read-only repository adapter backed by dynamic-app ORM rows.

    Query methods preserve all rows returned by the database so the application
    service can detect ambiguity and validate immutable history instead of
    silently selecting a first row.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_definitions(self, *, tenant_id: str, app_id: str) -> Sequence[AppDefinition]:
        result = await self._session.execute(
            select(AppDefinitionRow)
            .where(AppDefinitionRow.tenant_id == tenant_id, AppDefinitionRow.app_id == app_id)
            .order_by(AppDefinitionRow.id)
        )
        return tuple(self._definition(row) for row in result.scalars().all())

    async def list_versions(
        self, *, app_definition_id: str, manifest_version: str
    ) -> Sequence[AppVersion]:
        result = await self._session.execute(
            select(AppVersionRow)
            .where(
                AppVersionRow.app_definition_id == app_definition_id,
                AppVersionRow.manifest_version == manifest_version,
            )
            .order_by(AppVersionRow.id)
        )
        return tuple(self._version(row) for row in result.scalars().all())

    async def list_definition_versions(self, *, app_definition_id: str) -> Sequence[AppVersion]:
        result = await self._session.execute(
            select(AppVersionRow)
            .where(AppVersionRow.app_definition_id == app_definition_id)
            .order_by(AppVersionRow.created_at, AppVersionRow.id)
        )
        return tuple(self._version(row) for row in result.scalars().all())

    async def resolve(self, query: DefinitionVersionQuery) -> DefinitionResolution:
        """Resolve through the M04.e service, quarantining malformed ORM data."""
        try:
            return await DynamicAppDefinitionService(self).resolve(query)
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            return DefinitionResolution(
                outcome=DefinitionResolutionOutcome.QUARANTINED,
                reason=DefinitionResolutionReason.INVALID_VERSION_HISTORY,
                evidence={"adapter_error": type(exc).__name__},
            )

    @staticmethod
    def _definition(row: AppDefinitionRow) -> AppDefinition:
        return AppDefinition(
            id=row.id,
            tenant_id=row.tenant_id,
            app_id=row.app_id,
            display_name=row.display_name,
            description=row.description,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            status=AppLifecycleStatus(row.status),
            current_version_id=row.current_version_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version(row: AppVersionRow) -> AppVersion:
        if not isinstance(row.manifest, Mapping):
            raise ValueError("manifest must be persisted as an object")
        manifest = dict(row.manifest)
        manifest_requested = manifest.get("requestedCapabilities")
        persisted_requested = row.requested_capabilities
        if not isinstance(manifest_requested, list) or not isinstance(persisted_requested, list):
            raise ValueError("requested capabilities must be persisted as arrays")
        if persisted_requested != manifest_requested:
            raise ValueError("manifest and persisted requested capabilities disagree")
        requested = tuple(RequestedCapability(**item) for item in persisted_requested)
        rollback = RollbackMetadata(
            from_version_id=row.rollback_from_version_id,
            to_version_id=row.rollback_to_version_id,
            reason=row.rollback_reason,
            occurred_at=row.rollback_at,
        )
        return AppVersion(
            id=row.id,
            app_definition_id=row.app_definition_id,
            manifest_version=row.manifest_version,
            artifact_digest=row.artifact_digest,
            manifest=manifest,
            requested_capabilities=requested,
            status=AppLifecycleStatus(row.status),
            rollback=rollback,
            created_at=row.created_at,
            published_at=row.published_at,
        )


__all__ = ["DynamicAppDefinitionRepositoryAdapter"]
