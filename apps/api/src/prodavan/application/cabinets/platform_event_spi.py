"""Cabinet consumer for platform lifecycle events (L06/L07 SPI).

Records into cabinet meta_audit_events without nested commit so callers can
keep a single transaction. Package handlers / MCP on_platform_event — later.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow


class CabinetPlatformEventSpi:
    """Deliver platform_events into cabinet-local audit (stub SPI)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def deliver(
        self,
        *,
        cabinet_id: str,
        event_id: str,
        event_type: str,
        actor_sub: str | None,
        payload: dict[str, Any] | None = None,
    ) -> dict:
        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is None:
            return {"delivered": False, "reason": "cabinet not found"}
        qschema = qident(inst.schema_name)
        await self._session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_audit_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    tool_name TEXT,
                    actor_sub TEXT,
                    detail JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        detail = {
            "platform_event_id": event_id,
            "platform_event_type": event_type,
            **(payload or {}),
        }
        audit_id = f"aud_{uuid.uuid4().hex[:12]}"
        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.meta_audit_events
                (id, event_type, tool_name, actor_sub, detail)
                VALUES (:id, :etype, :tool, :sub, CAST(:detail AS jsonb))
                """
            ),
            {
                "id": audit_id,
                "etype": "platform_event.delivered",
                "tool": "on_platform_event",
                "sub": actor_sub or "system",
                "detail": json.dumps(detail, ensure_ascii=False),
            },
        )
        await self._session.flush()
        return {"delivered": True, "audit_id": audit_id, "cabinet_id": cabinet_id}
