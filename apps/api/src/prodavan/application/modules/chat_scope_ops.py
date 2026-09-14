"""Shared helpers to apply chat scope on module data CRUD paths."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.chat_scope import (
    CHAT_SCOPE_CURRENT,
    assert_row_session_access,
    chats_scope_from_tables_body,
    require_session_id,
    stamp_session_on_body,
)
from prodavan.application.modules.module_instance_service import ModuleInstanceService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow


async def resolve_table_chats_scope(
    instances: ModuleInstanceService,
    *,
    instance_id: str,
    module_id: str,
    table_slug: str,
) -> str:
    tables_body = await instances.resolve_tables_body(
        instance_id=instance_id, module_id=module_id
    )
    return chats_scope_from_tables_body(tables_body, table_slug)


async def ensure_session_belongs_to_project(
    session: AsyncSession,
    *,
    project_id: str,
    session_id: str,
) -> None:
    q = await session.execute(
        select(AgentSessionRow.id).where(
            AgentSessionRow.id == session_id,
            AgentSessionRow.project_id == project_id,
        )
    )
    if q.scalar_one_or_none() is None:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="session_id does not belong to project",
        )


async def prepare_chat_scoped_list(
    instances: ModuleInstanceService,
    *,
    instance_id: str,
    module_id: str,
    table_slug: str,
    session_id: str | None,
    db: AsyncSession | None = None,
    project_id: str | None = None,
) -> tuple[str | None, bool]:
    """Return (filter_session_id, skip_empty).

    skip_empty True → caller should return [] without querying.
    """
    chats = await resolve_table_chats_scope(
        instances, instance_id=instance_id, module_id=module_id, table_slug=table_slug
    )
    if chats != CHAT_SCOPE_CURRENT:
        return None, False
    sid = require_session_id(session_id, for_write=False)
    if sid is None:
        return None, True
    if db is not None and project_id is not None:
        await ensure_session_belongs_to_project(db, project_id=project_id, session_id=sid)
    return sid, False


async def prepare_chat_scoped_write(
    instances: ModuleInstanceService,
    *,
    instance_id: str,
    module_id: str,
    table_slug: str,
    body: dict[str, Any],
    session_id: str | None,
    db: AsyncSession,
    project_id: str,
    existing_row: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Stamp/validate session for write. Returns (body, session_id_or_none)."""
    chats = await resolve_table_chats_scope(
        instances, instance_id=instance_id, module_id=module_id, table_slug=table_slug
    )
    if chats != CHAT_SCOPE_CURRENT:
        # Shared tables: strip accidental session_id from client body.
        cleaned = {k: v for k, v in body.items() if k != "session_id"}
        return cleaned, None
    sid = require_session_id(session_id or body.get("session_id"), for_write=True)
    assert sid is not None
    await ensure_session_belongs_to_project(db, project_id=project_id, session_id=sid)
    if existing_row is not None:
        body_existing = existing_row.get("body")
        nested = (
            body_existing.get("session_id")
            if isinstance(body_existing, dict)
            else None
        )
        assert_row_session_access(
            row_session_id=existing_row.get("session_id") or nested,
            active_session_id=sid,
        )
    return stamp_session_on_body(body, sid), sid
