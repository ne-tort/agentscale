"""Unified workspace sync policy after cabinet/module changes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.project_service.query import ProjectQuery
from prodavan.application.projects.workspace_outdated import (
    mark_workspace_outdated_for_cabinet,
    mark_workspace_outdated_for_project,
)
from prodavan.config.settings import settings
from prodavan.core.jobs.rematerialize_bus import request_rematerialize_project
from prodavan.domain.projects import ProjectStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkspaceSyncNotification:
    mode: Literal["deferred", "scheduled"]
    marked_outdated: int = 0
    scheduled: int = 0
    cabinet_id: str | None = None
    project_id: str | None = None
    module_id: str | None = None
    source: str | None = None
    enqueued: tuple[str, ...] = ()
    sync: tuple[str, ...] = ()
    skipped: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mode": self.mode,
            "marked_outdated": self.marked_outdated,
            "scheduled": self.scheduled,
        }
        if self.cabinet_id is not None:
            out["cabinet_id"] = self.cabinet_id
        if self.project_id is not None:
            out["project_id"] = self.project_id
        if self.module_id is not None:
            out["module_id"] = self.module_id
        if self.source is not None:
            out["source"] = self.source
        if self.enqueued:
            out["enqueued"] = list(self.enqueued)
        if self.sync:
            out["sync"] = list(self.sync)
        if self.skipped:
            out["skipped"] = True
        if self.reason is not None:
            out["reason"] = self.reason
        return out

    def rematerialize_alias(self) -> dict[str, Any]:
        """Backward-compatible rematerialize key shape."""
        out = self.as_dict()
        if self.mode == "deferred":
            out.setdefault("scheduled", 0)
        return out


def attach_workspace_sync(row: dict[str, Any], notification: WorkspaceSyncNotification) -> dict[str, Any]:
    out = dict(row)
    payload = notification.as_dict()
    if notification.mode == "scheduled" and notification.scheduled <= 0:
        if notification.marked_outdated <= 0 and not notification.skipped:
            return out
    if notification.mode == "deferred" and notification.marked_outdated <= 0:
        if notification.skipped:
            out["workspace_sync"] = payload
            out["rematerialize"] = notification.rematerialize_alias()
        return out
    out["workspace_sync"] = payload
    out["rematerialize"] = notification.rematerialize_alias()
    return out


async def defer_or_schedule_project_sync(
    session: AsyncSession,
    *,
    project_id: str,
    source: str,
) -> WorkspaceSyncNotification:
    if settings.projects_auto_rematerialize_on_cabinet_change:
        result = await request_rematerialize_project(project_id, source=source)
        if result.get("enqueued"):
            return WorkspaceSyncNotification(
                mode="scheduled",
                scheduled=1,
                project_id=project_id,
                source=source,
                enqueued=(project_id,),
            )
        if result.get("reason") == "celery_disabled":
            try:
                from prodavan.application.project_service.command import ProjectCommand

                await ProjectCommand(session).rematerialize_background(project_id=project_id)
                return WorkspaceSyncNotification(
                    mode="scheduled",
                    scheduled=1,
                    project_id=project_id,
                    source=source,
                    sync=(project_id,),
                )
            except Exception:
                logger.exception("inline rematerialize failed project_id=%s", project_id)
        return WorkspaceSyncNotification(
            mode="scheduled",
            scheduled=0,
            project_id=project_id,
            source=source,
            skipped=True,
            reason=str(result.get("reason") or "not_enqueued"),
        )

    outdated = await mark_workspace_outdated_for_project(session, project_id=project_id)
    return WorkspaceSyncNotification(
        mode="deferred",
        marked_outdated=int(outdated.get("marked_outdated") or 0),
        project_id=project_id,
        source=source,
    )


async def defer_or_schedule_cabinet_sync(
    session: AsyncSession,
    *,
    cabinet_id: str,
    module_id: str | None,
    source: str,
    project_ids: list[str] | None = None,
) -> WorkspaceSyncNotification:
    if settings.projects_auto_rematerialize_on_cabinet_change:
        all_ids = await ProjectQuery(session).list_ids(
            cabinet_id=cabinet_id,
            exclude_status=ProjectStatus.DELETED,
        )
        if project_ids is not None:
            allow = set(project_ids)
            all_ids = [pid for pid in all_ids if pid in allow]
        enqueued: list[str] = []
        synced: list[str] = []
        for project_id in all_ids:
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
                    from prodavan.application.project_service.command import ProjectCommand

                    await ProjectCommand(session).rematerialize_background(project_id=project_id)
                    synced.append(project_id)
                except Exception:
                    logger.exception(
                        "inline rematerialize failed cabinet_id=%s project_id=%s",
                        cabinet_id,
                        project_id,
                    )
        scheduled = len(enqueued) + len(synced)
        return WorkspaceSyncNotification(
            mode="scheduled",
            scheduled=scheduled,
            cabinet_id=cabinet_id,
            module_id=module_id,
            source=source,
            enqueued=tuple(enqueued),
            sync=tuple(synced),
        )

    outdated = await mark_workspace_outdated_for_cabinet(
        session,
        cabinet_id=cabinet_id,
        source=source,
        project_ids=project_ids,
    )
    return WorkspaceSyncNotification(
        mode="deferred",
        marked_outdated=int(outdated.get("marked_outdated") or 0),
        cabinet_id=cabinet_id,
        module_id=module_id,
        source=source,
    )
