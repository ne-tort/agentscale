"""Platform audit trail for AI Provider Keys mutations (L03)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.identity import Principal

_MAX_AUDIT = 200
_SENSITIVE_KEYS = frozenset({"secret", "password", "token", "zip_base64"})


def _sanitize_detail(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in raw.items():
        if key in _SENSITIVE_KEYS:
            out[key] = "<redacted>" if not isinstance(val, str) else f"<redacted len={len(val)}>"
            continue
        if isinstance(val, str) and len(val) > 256:
            out[key] = val[:256] + "…"
        else:
            out[key] = val
    return out


class AiKeyAuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _ensure_table(self) -> None:
        await self._session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ai_key_audit_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    key_id TEXT,
                    actor_sub TEXT,
                    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )

    async def record(
        self,
        *,
        event_type: str,
        key_id: str | None,
        principal: Principal,
        detail: dict[str, Any] | None = None,
    ) -> None:
        await self._ensure_table()
        event_id = f"aae_{uuid.uuid4().hex[:12]}"
        payload = _sanitize_detail(detail or {})
        await self._session.execute(
            text(
                """
                INSERT INTO ai_key_audit_events
                (id, event_type, key_id, actor_sub, detail)
                VALUES (:id, :etype, :kid, :sub, CAST(:detail AS jsonb))
                """
            ),
            {
                "id": event_id,
                "etype": event_type,
                "kid": key_id,
                "sub": principal.sub,
                "detail": json.dumps(payload, ensure_ascii=False),
            },
        )
        await self._session.commit()

    async def list_events(
        self,
        *,
        key_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        await self._ensure_table()
        if limit < 1 or limit > _MAX_AUDIT:
            limit = 50
        if key_id:
            q = await self._session.execute(
                text(
                    """
                    SELECT id, event_type, key_id, actor_sub, detail, created_at
                    FROM ai_key_audit_events
                    WHERE key_id = :kid
                    ORDER BY created_at DESC
                    LIMIT :lim
                    """
                ),
                {"kid": key_id, "lim": limit},
            )
        else:
            q = await self._session.execute(
                text(
                    """
                    SELECT id, event_type, key_id, actor_sub, detail, created_at
                    FROM ai_key_audit_events
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
                    "key_id": r.key_id,
                    "actor_sub": r.actor_sub,
                    "detail": detail if isinstance(detail, dict) else {},
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return out
