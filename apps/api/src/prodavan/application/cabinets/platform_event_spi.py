"""Cabinet consumer for platform lifecycle events (L06/L07 SPI).

Records into cabinet meta_audit_events without nested commit so callers can
keep a single transaction. Package handlers: audit + optional opt-in invoke of
`src/on_platform_event.py` from deployed zip (MCP_PLATFORM_EVENT_INVOKE).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.packages_service import CabinetPackagesService
from prodavan.config.settings import settings
from prodavan.infrastructure.cabinets.platform_event_handler import invoke_platform_event_from_artifact
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow


def package_subscribes_to_event(manifest: dict[str, Any] | None, event_type: str) -> bool:
    """True when package manifest lists event_type or wildcard in platform_events."""
    if not manifest:
        return False
    raw = manifest.get("platform_events")
    if raw is None:
        raw = manifest.get("on_platform_event")
    if raw is None:
        return False
    if isinstance(raw, str):
        subscribed = [raw]
    elif isinstance(raw, list):
        subscribed = [str(x) for x in raw if x]
    else:
        return False
    return "*" in subscribed or event_type in subscribed


class CabinetPlatformEventSpi:
    """Deliver platform_events into cabinet-local audit + optional package hooks."""

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
        package_handlers = await self._invoke_package_handlers(
            cabinet_id=cabinet_id,
            qschema=qschema,
            event_id=event_id,
            event_type=event_type,
            actor_sub=actor_sub,
            payload=payload,
        )
        await self._session.flush()
        out: dict[str, Any] = {"delivered": True, "audit_id": audit_id, "cabinet_id": cabinet_id}
        if package_handlers:
            out["package_handlers"] = package_handlers
        return out

    async def _invoke_package_handlers(
        self,
        *,
        cabinet_id: str,
        qschema: str,
        event_id: str,
        event_type: str,
        actor_sub: str | None,
        payload: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        q = await self._session.execute(
            text(
                f"""
                SELECT name, version, manifest, artifact_ref
                FROM {qschema}.meta_mcp_packages
                WHERE status = 'active'
                ORDER BY name, version
                """
            )
        )
        invoked: list[dict[str, Any]] = []
        event_body = {
            "platform_event_id": event_id,
            "platform_event_type": event_type,
            **(payload or {}),
        }
        for row in q.fetchall():
            manifest = row.manifest
            if isinstance(manifest, str):
                try:
                    manifest = json.loads(manifest)
                except json.JSONDecodeError:
                    manifest = {}
            if not isinstance(manifest, dict):
                manifest = {}
            if not package_subscribes_to_event(manifest, event_type):
                continue

            handler_result: dict[str, Any] = {"action": "stub"}
            if settings.mcp_platform_event_invoke:
                artifact_path = CabinetPackagesService._path_from_ref(row.artifact_ref)
                handler_result = await asyncio.to_thread(
                    invoke_platform_event_from_artifact,
                    artifact_path=artifact_path,
                    package_name=row.name,
                    event=event_body,
                )

            handler_detail = {
                **event_body,
                "package_name": row.name,
                "package_version": row.version,
                **handler_result,
            }
            handler_audit_id = f"aud_{uuid.uuid4().hex[:12]}"
            await self._session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.meta_audit_events
                    (id, event_type, tool_name, actor_sub, detail)
                    VALUES (:id, :etype, :tool, :sub, CAST(:detail AS jsonb))
                    """
                ),
                {
                    "id": handler_audit_id,
                    "etype": "platform_event.package_handler",
                    "tool": "on_platform_event",
                    "sub": actor_sub or "system",
                    "detail": json.dumps(handler_detail, ensure_ascii=False),
                },
            )
            invoked.append(
                {
                    "package": row.name,
                    "version": row.version,
                    "audit_id": handler_audit_id,
                    "action": handler_result.get("action"),
                }
            )
        return invoked
