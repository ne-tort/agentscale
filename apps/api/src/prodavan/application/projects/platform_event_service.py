"""Platform lifecycle event bus (L07) — separate from project triggers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import PLATFORM_EVENT_TYPES
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.platform_events import PlatformEventRow


class PlatformEventService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _public(row: PlatformEventRow) -> dict:
        return {
            "id": row.id,
            "event_type": row.event_type,
            "company_id": row.company_id,
            "project_id": row.project_id,
            "cabinet_id": row.cabinet_id,
            "actor_sub": row.actor_sub,
            "payload": row.payload,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    async def _cabinet_ids_for_delivery(
        self,
        *,
        company_id: str | None,
        cabinet_id: str | None,
    ) -> list[str]:
        if cabinet_id:
            return [cabinet_id]
        if not company_id:
            return []
        q = await self._session.execute(
            select(CabinetInstanceRow.id).where(
                CabinetInstanceRow.company_id == company_id,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        return list(q.scalars().all())

    async def emit(
        self,
        *,
        event_type: str,
        company_id: str | None = None,
        project_id: str | None = None,
        cabinet_id: str | None = None,
        principal: Principal | None = None,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        if event_type not in PLATFORM_EVENT_TYPES:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"unsupported platform event type: {event_type}",
            )
        if idempotency_key:
            existing = await self._session.scalar(
                select(PlatformEventRow).where(
                    PlatformEventRow.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return self._public(existing)

        row = PlatformEventRow(
            event_type=event_type,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            actor_sub=principal.sub if principal else None,
            idempotency_key=idempotency_key,
            payload=payload or {},
        )
        try:
            if idempotency_key:
                # Put both the INSERT and its flush inside the savepoint. A
                # concurrent emitter can win the unique constraint; rolling
                # back only this nested transaction keeps the caller's
                # transaction usable for the replay lookup below.
                async with self._session.begin_nested():
                    self._session.add(row)
                    await self._session.flush()
            else:
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            if not idempotency_key:
                raise
            existing = await self._session.scalar(
                select(PlatformEventRow).where(
                    PlatformEventRow.idempotency_key == idempotency_key
                )
            )
            if existing is None:
                raise
            return self._public(existing)
        deliveries: list[dict] = []
        # Package SPI removed with MCP packages; platform events stay in DB only.
        out = self._public(row)
        if deliveries:
            out["cabinet_deliveries"] = deliveries
            if cabinet_id and len(deliveries) == 1:
                out["cabinet_delivery"] = deliveries[0]
        from prodavan.core.events.deferred import schedule_platform_event_publish

        schedule_platform_event_publish(
            self._session,
            event_id=row.id,
            event_type=row.event_type,
            company_id=row.company_id,
            project_id=row.project_id,
            cabinet_id=row.cabinet_id,
            payload=row.payload if isinstance(row.payload, dict) else {},
            occurred_at=out.get("created_at"),
        )
        return out

    async def list_events(
        self,
        *,
        company_id: str | None = None,
        project_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        lim = max(1, min(limit, 200))
        stmt = select(PlatformEventRow)
        if company_id:
            stmt = stmt.where(PlatformEventRow.company_id == company_id)
        if project_id:
            stmt = stmt.where(PlatformEventRow.project_id == project_id)
        if event_type:
            stmt = stmt.where(PlatformEventRow.event_type == event_type)
        stmt = stmt.order_by(PlatformEventRow.created_at.desc()).limit(lim)
        q = await self._session.execute(stmt)
        return [
            {
                "id": r.id,
                "event_type": r.event_type,
                "company_id": r.company_id,
                "project_id": r.project_id,
                "cabinet_id": r.cabinet_id,
                "actor_sub": r.actor_sub,
                "payload": r.payload,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in q.scalars().all()
        ]
