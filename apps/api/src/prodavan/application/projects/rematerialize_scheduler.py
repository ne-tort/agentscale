"""Schedule project rematerialize after cabinet module data changes (C-JOBS)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.projects.workspace_sync_policy import defer_or_schedule_cabinet_sync

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
    """Enqueue rematerialize or mark workspace outdated when auto-sync is disabled."""
    if not await module_has_materialize_rules(session, module_id=module_id):
        return {"scheduled": 0, "skipped": True, "reason": "no_materialize_rules"}

    notification = await defer_or_schedule_cabinet_sync(
        session,
        cabinet_id=cabinet_id,
        module_id=module_id,
        source="cabinet_module",
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
