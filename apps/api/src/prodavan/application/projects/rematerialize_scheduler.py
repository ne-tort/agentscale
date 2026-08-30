"""Schedule project rematerialize after cabinet module data changes (C-JOBS)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.project_service.command import ProjectCommand
from prodavan.application.project_service.query import ProjectQuery
from prodavan.core.jobs.rematerialize_bus import request_rematerialize_project
from prodavan.domain.projects import ProjectStatus

logger = logging.getLogger(__name__)

_MATERIALIZE_MODULE_IDS = frozenset({"mod_files", "mod_mcp", "mod_prompts"})


async def module_has_materialize_rules(session: AsyncSession, *, module_id: str) -> bool:
    if module_id in _MATERIALIZE_MODULE_IDS:
        return True
    try:
        doc = await ModuleMetaDocumentService(session).get_document(module_id=module_id, slug="materialize")
    except Exception:
        return False
    body = doc.get("body")
    return isinstance(body, list) and len(body) > 0


async def schedule_cabinet_rematerialize(
    session: AsyncSession,
    *,
    cabinet_id: str,
    module_id: str,
) -> dict[str, Any]:
    """Enqueue (or inline fallback) rematerialize for all live projects in cabinet."""
    if not await module_has_materialize_rules(session, module_id=module_id):
        return {"scheduled": 0, "skipped": True, "reason": "no_materialize_rules"}

    return await _schedule_cabinet_projects_rematerialize(
        session,
        cabinet_id=cabinet_id,
        module_id=module_id,
        source="cabinet_module",
    )


async def schedule_cabinet_binding_change_rematerialize(
    session: AsyncSession,
    *,
    cabinet_id: str,
) -> dict[str, Any]:
    """Rematerialize all projects after cabinet↔module binding changes (install/uninstall/prune)."""
    return await _schedule_cabinet_projects_rematerialize(
        session,
        cabinet_id=cabinet_id,
        module_id=None,
        source="cabinet_binding",
    )


async def _schedule_cabinet_projects_rematerialize(
    session: AsyncSession,
    *,
    cabinet_id: str,
    module_id: str | None,
    source: str,
) -> dict[str, Any]:
    project_ids = await ProjectQuery(session).list_ids(
        cabinet_id=cabinet_id,
        exclude_status=ProjectStatus.DELETED,
    )
    enqueued: list[str] = []
    synced: list[str] = []
    for project_id in project_ids:
        result = await request_rematerialize_project(
            project_id,
            cabinet_id=cabinet_id,
            module_id=module_id,
            source=source,
        )
        if result.get("enqueued"):
            enqueued.append(project_id)
            continue
        if result.get("reason") == "celery_disabled":
            try:
                await ProjectCommand(session).rematerialize_background(project_id=project_id)
                synced.append(project_id)
            except Exception:
                logger.exception(
                    "inline rematerialize failed cabinet_id=%s project_id=%s",
                    cabinet_id,
                    project_id,
                )
    out: dict[str, Any] = {
        "scheduled": len(enqueued) + len(synced),
        "enqueued": enqueued,
        "sync": synced,
        "cabinet_id": cabinet_id,
    }
    if module_id is not None:
        out["module_id"] = module_id
    return out
