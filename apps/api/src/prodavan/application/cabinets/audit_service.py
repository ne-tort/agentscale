"""Cabinet audit trail for MCP and meta mutations (L06 subset)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_MAX_AUDIT = 200
_SENSITIVE_KEYS = frozenset({"zip_base64", "password", "secret", "token"})


def _sanitize_detail(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in raw.items():
        if key in _SENSITIVE_KEYS:
            if isinstance(val, str):
                out[key] = f"<redacted len={len(val)}>"
            else:
                out[key] = "<redacted>"
            continue
        if isinstance(val, str) and len(val) > 256:
            out[key] = val[:256] + "…"
        else:
            out[key] = val
    return out


class CabinetAuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)

    async def _ensure_table(self, schema_name: str) -> None:
        qschema = qident(schema_name)
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

    async def record(
        self,
        *,
        cabinet_id: str,
        event_type: str,
        tool_name: str | None,
        principal: Principal,
        detail: dict[str, Any] | None = None,
    ) -> None:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=None,
            write=False,
        )
        await self._ensure_table(inst.schema_name)
        qschema = qident(inst.schema_name)
        event_id = f"aud_{uuid.uuid4().hex[:12]}"
        payload = _sanitize_detail(detail or {})
        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.meta_audit_events
                (id, event_type, tool_name, actor_sub, detail)
                VALUES (:id, :etype, :tool, :sub, CAST(:detail AS jsonb))
                """
            ),
            {
                "id": event_id,
                "etype": event_type,
                "tool": tool_name,
                "sub": principal.sub,
                "detail": json.dumps(payload, ensure_ascii=False),
            },
        )
        await self._session.commit()

    async def list_events(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
    ) -> list[dict]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        await self._ensure_table(inst.schema_name)
        if limit < 1 or limit > _MAX_AUDIT:
            limit = 50
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT id, event_type, tool_name, actor_sub, detail, created_at
                FROM {qschema}.meta_audit_events
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        )
        out: list[dict] = []
        for r in q.fetchall():
            detail = r.detail
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail)
                except json.JSONDecodeError:
                    detail = {}
            out.append(
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "tool_name": r.tool_name,
                    "actor_sub": r.actor_sub,
                    "detail": detail if isinstance(detail, dict) else {},
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return out
