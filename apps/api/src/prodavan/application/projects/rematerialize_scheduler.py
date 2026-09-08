"""Schedule project rematerialize after cabinet module data changes (C-JOBS)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.projects.workspace_sync_policy import defer_or_schedule_cabinet_sync

logger = logging.getLogger(__name__)

_MATERIALIZE_MODULE_IDS = frozenset()  # prefer meta slug materialize; kept empty on purpose


async def module_has_materialize_rules(session: AsyncSession, *, module_id: str) -> bool:
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
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Enqueue rematerialize for module-bound projects (optional project_ids filter)."""
    if not await module_has_materialize_rules(session, module_id=module_id):
        return {"scheduled": 0, "skipped": True, "reason": "no_materialize_rules"}

    from prodavan.application.modules.module_binding_service import ModuleBindingService
    from prodavan.infrastructure.persistence.models.projects import ProjectRow
    from sqlalchemy import select

    bindings = ModuleBindingService(session)
    bound = set(await bindings.list_project_ids(module_id))
    if project_ids is not None:
        wanted = set(str(p) for p in project_ids) & bound
    else:
        # Only projects in this cabinet that are bound to the module.
        q = await session.execute(
            select(ProjectRow.id).where(ProjectRow.cabinet_id == cabinet_id)
        )
        wanted = {str(pid) for (pid,) in q.all()} & bound

    if not wanted:
        return {"scheduled": 0, "skipped": True, "reason": "no_bound_projects"}

    # Defer/schedule per project via cabinet sync filtered by wanted set.
    notification = await defer_or_schedule_cabinet_sync(
        session,
        cabinet_id=cabinet_id,
        module_id=module_id,
        source="cabinet_module",
        project_ids=list(wanted),
    )
    return notification.rematerialize_alias()


async def schedule_cabinet_binding_change_rematerialize(
    session: AsyncSession,
    *,
    cabinet_id: str,
) -> dict[str, Any]:
    """Rematerialize or mark outdated after cabinet↔module binding changes."""
    notification = await defer_or_schedule_cabinet_sync(
        session,
        cabinet_id=cabinet_id,
        module_id=None,
        source="cabinet_binding",
    )
    return notification.rematerialize_alias()
