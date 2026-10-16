"""Проактивный перевыпуск bridge-токена подов до истечения (24ч).

Токен моста пода живёт 24 ч; в простаивающем поде он протухает, и первый
MCP-вызов после простоя отвечает 401 «pod bridge token expired». Sweep проходит
по проектам с живым подом и вызывает ensure_recent_bind: тот сам себя
троттлит меткой (12 ч) и реально перепривязывает под только когда метка
протухла — то есть токен пода обновляется задолго до его истечения.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.openclaw_bridge import ensure_recent_bind
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow

logger = logging.getLogger(__name__)


async def sweep_bind_refresh(session: AsyncSession) -> dict[str, Any]:
    """Перевыпуск bridge-токенов проектов с живым подом (до истечения)."""
    q = await session.execute(
        select(ProjectPodRow.project_id).where(ProjectPodRow.status == "running")
    )
    project_ids = [str(r[0]) for r in q.all() if r[0]]
    rebound = 0
    for project_id in project_ids:
        try:
            # force=False: ensure_recent_bind сам решает по метке (12 ч) —
            # перепривязывает только когда прошлый bind протух
            await ensure_recent_bind(session, project_id)
            rebound += 1
        except Exception:
            logger.debug("bind refresh failed project_id=%s", project_id, exc_info=True)
    return {"projects": len(project_ids), "rebound": rebound}
