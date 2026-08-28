"""Project lifecycle events — platform bus facade."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.platform_event_service import PlatformEventService
from prodavan.domain.identity import Principal


class ProjectLifecycleEmitter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._platform = PlatformEventService(session)

    async def emit(
        self,
        *,
        event_type: str,
        company_id: str,
        project_id: str,
        cabinet_id: str,
        principal: Principal,
        payload: dict | None = None,
    ) -> None:
        await self._platform.emit(
            event_type=event_type,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            principal=principal,
            payload=payload,
        )
