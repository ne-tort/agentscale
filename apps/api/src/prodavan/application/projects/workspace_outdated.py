"""Mark projects whose workspace needs explicit sync (L07)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.project_service.query import ProjectQuery
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.modules import ModuleProjectBindingRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


async def mark_workspace_outdated_for_cabinet(
    session: AsyncSession,
    *,
    cabinet_id: str,
    source: str,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    all_ids = await ProjectQuery(session).list_ids(
        cabinet_id=cabinet_id,
        exclude_status=ProjectStatus.DELETED,
    )
    if project_ids is not None:
        allow = set(project_ids)
        all_ids = [pid for pid in all_ids if pid in allow]
    now = datetime.now(UTC)
    marked = 0
    for project_id in all_ids:
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
        "project_ids": all_ids,
    }


async def mark_workspace_outdated_for_module(
    session: AsyncSession,
    *,
    module_id: str,
    source: str,
) -> dict[str, Any]:
    """Mark all projects bound to ``module_id`` as needing workspace sync."""
    q = await session.execute(
        select(ModuleProjectBindingRow.project_id).where(
            ModuleProjectBindingRow.module_id == module_id
        )
    )
    project_ids = list(dict.fromkeys(q.scalars().all()))
    now = datetime.now(UTC)
    marked = 0
    marked_ids: list[str] = []
    for project_id in project_ids:
        row = await session.get(ProjectRow, project_id)
        if row is None or row.status == ProjectStatus.DELETED:
            continue
        row.workspace_outdated_at = now
        marked += 1
        marked_ids.append(project_id)
    return {
        "scheduled": 0,
        "marked_outdated": marked,
        "module_id": module_id,
        "source": source,
        "project_ids": marked_ids,
    }


async def mark_workspace_outdated_for_project(session: AsyncSession, *, project_id: str) -> dict[str, Any]:
    row = await session.get(ProjectRow, project_id)
    if row is None:
        return {"marked_outdated": 0, "project_id": project_id}
    row.workspace_outdated_at = datetime.now(UTC)
    return {"marked_outdated": 1, "project_id": project_id}


def clear_workspace_outdated(row: ProjectRow) -> None:
    row.workspace_outdated_at = None


# ---------------------------------------------------------------- WAVE16: pending sync

async def mark_workspace_sync_pending(
    session: AsyncSession,
    *,
    project_id: str,
    source: str,
) -> bool:
    """Отложить sync: агент в ходу — rematerialize сработает по окончании хода."""
    row = await session.get(ProjectRow, project_id)
    if row is None or row.status == ProjectStatus.DELETED:
        return False
    row.workspace_sync_pending_at = datetime.now(UTC)
    row.workspace_sync_pending_source = source
    return True


async def take_workspace_sync_pending(
    session: AsyncSession,
    *,
    project_id: str,
) -> str | None:
    """Снять метку pending (атомарно в рамках транзакции); вернуть source или None."""
    row = await session.get(ProjectRow, project_id)
    if row is None or row.workspace_sync_pending_at is None:
        return None
    source = row.workspace_sync_pending_source or "unknown"
    row.workspace_sync_pending_at = None
    row.workspace_sync_pending_source = None
    return source


async def list_workspace_sync_pending(
    session: AsyncSession,
    *,
    older_than_minutes: int = 30,
) -> list[str]:
    """Проекты с зависшим pending (turn-end не дошёл — воркер упал, рестарт)."""
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    q = await session.execute(
        select(ProjectRow.id).where(
            ProjectRow.workspace_sync_pending_at.is_not(None),
            ProjectRow.workspace_sync_pending_at < cutoff,
            ProjectRow.status != ProjectStatus.DELETED,
        )
    )
    return [str(r) for r in q.scalars().all()]
