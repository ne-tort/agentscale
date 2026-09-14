"""Lifecycle purge for all Tenant Infra planes."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def purge_project_tenant_infra(
    *,
    company_id: str,
    project_id: str,
    session: AsyncSession | None = None,
) -> dict[str, int]:
    """Best-effort cleanup on pause/stop/purge — never raises to callers."""
    stats: dict[str, int] = {}
    try:
        from prodavan.application.tenant_infra.service import TenantInfraService

        stats["cache"] = await TenantInfraService().purge_project(
            company_id=company_id, project_id=project_id
        )
    except Exception:
        logger.exception("tenant_infra cache purge failed project=%s", project_id)
        stats["cache"] = 0
    try:
        from prodavan.application.tenant_infra.docs_service import TenantDocsService

        stats["docs"] = await TenantDocsService().purge_project(
            company_id=company_id, project_id=project_id
        )
    except Exception:
        logger.exception("tenant_infra docs purge failed project=%s", project_id)
        stats["docs"] = 0
    try:
        from prodavan.application.tenant_infra.userdb_service import TenantUserDbService

        stats["userdb"] = await TenantUserDbService().purge_project(
            company_id=company_id, project_id=project_id
        )
    except Exception:
        logger.exception("tenant_infra userdb purge failed project=%s", project_id)
        stats["userdb"] = 0
    try:
        from prodavan.application.tenant_infra.events_service import TenantEventsService

        stats["events"] = await TenantEventsService().purge_project(
            company_id=company_id, project_id=project_id
        )
    except Exception:
        logger.exception("tenant_infra events purge failed project=%s", project_id)
        stats["events"] = 0
    if session is not None:
        try:
            from prodavan.application.tenant_infra.objects_service import TenantObjectsService

            stats["objects"] = await TenantObjectsService(session).purge_project(
                company_id=company_id, project_id=project_id
            )
        except Exception:
            logger.exception("tenant_infra objects purge failed project=%s", project_id)
            stats["objects"] = 0
        try:
            from prodavan.application.modules.equipment_catalog_opensearch import (
                cleanup_equipment_indexes_for_instance,
            )
            from prodavan.application.modules.module_instance_service import (
                OWNER_PROJECT,
                ModuleInstanceService,
            )

            # Local leaf only — global MP shares cabinet indexes (do not delete).
            leaf = await ModuleInstanceService(session).get_instance(
                owner_kind=OWNER_PROJECT,
                owner_id=project_id,
                module_id="mod_equipment",
            )
            if leaf is not None:
                deleted = await cleanup_equipment_indexes_for_instance(
                    session=session,
                    instance_id=leaf.id,
                    company_id=company_id,
                    project_id=project_id,
                )
                stats["equipment_os"] = len(deleted)
            else:
                stats["equipment_os"] = 0
        except Exception:
            logger.exception("equipment OS purge failed project=%s", project_id)
            stats["equipment_os"] = 0
    return stats
