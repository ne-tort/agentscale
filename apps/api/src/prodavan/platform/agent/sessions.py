"""In-memory agent session store (platform layer). Persist later via Platform DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


class AgentSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def create(
        self,
        *,
        tenant_id: UUID,
        cabinet_id: UUID,
        project_id: str,
        user_id: UUID,
        allowed_tools: list[str],
        pack_id: str,
    ) -> dict[str, Any]:
        sid = str(uuid.uuid4())
        session = {
            "id": sid,
            "tenant_id": str(tenant_id),
            "cabinet_id": str(cabinet_id),
            "project_id": project_id,
            "user_id": str(user_id),
            "pack_id": pack_id,
            "allowed_tools": [t for t in allowed_tools if t],
            "status": "active",
            "messages": [],
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self._sessions.get(session_id)

    def append_message(self, session_id: str, message: dict[str, Any]) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.setdefault("messages", []).append(message)
