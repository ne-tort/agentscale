"""Pod lifecycle events — platform bus facade."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.platform_event_service import PlatformEventService
from prodavan.domain.identity import Principal


class PodLifecycleEmitter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._platform = PlatformEventService(session)

    async def emit(
        self,
        *,
        event_type: str,
        company_id: str,
        project_id: str | None,
        cabinet_id: str | None,
        principal: Principal,
        pod_id: str,
        payload: dict | None = None,
    ) -> None:
        body = {"pod_id": pod_id, **(payload or {})}
        await self._platform.emit(
            event_type=event_type,
            company_id=company_id,
            project_id=project_id or "",
            cabinet_id=cabinet_id or "",
            principal=principal,
            payload=body,
        )
