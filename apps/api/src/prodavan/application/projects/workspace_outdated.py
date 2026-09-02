"""Mark projects whose workspace needs explicit sync (L07)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.project_service.query import ProjectQuery
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectRow


async def mark_workspace_outdated_for_cabinet(
    session: AsyncSession,
    *,
    cabinet_id: str,
    source: str,
) -> dict[str, Any]:
    project_ids = await ProjectQuery(session).list_ids(
        cabinet_id=cabinet_id,
        exclude_status=ProjectStatus.DELETED,
    )
    now = datetime.now(UTC)
    marked = 0
    for project_id in project_ids:
        row = await session.get(ProjectRow, project_id)
        if row is None:
            continue
        row.workspace_outdated_at = now
        marked += 1
    return {
        "scheduled": 0,
        "marked_outdated": marked,
        "cabinet_id": cabinet_id,
        "source": source,
        "project_ids": project_ids,
    }


async def mark_workspace_outdated_for_project(session: AsyncSession, *, project_id: str) -> dict[str, Any]:
    row = await session.get(ProjectRow, project_id)
    if row is None:
        return {"marked_outdated": 0, "project_id": project_id}
    row.workspace_outdated_at = datetime.now(UTC)
    return {"marked_outdated": 1, "project_id": project_id}


def clear_workspace_outdated(row: ProjectRow) -> None:
    row.workspace_outdated_at = None
