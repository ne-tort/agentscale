"""Apply PostgreSQL session GUCs for RLS."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def apply_rls(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    cabinet_id: uuid.UUID | None = None,
    local: bool = False,
) -> None:
    is_local = "true" if local else "false"
    await session.execute(
        text(f"SELECT set_config('app.user_id', :user_id, {is_local})"),
        {"user_id": str(user_id)},
    )
    await session.execute(
        text(f"SELECT set_config('app.tenant_id', :tenant_id, {is_local})"),
        {"tenant_id": str(tenant_id) if tenant_id else ""},
    )
    await session.execute(
        text(f"SELECT set_config('app.cabinet_id', :cabinet_id, {is_local})"),
        {"cabinet_id": str(cabinet_id) if cabinet_id else ""},
    )


async def reset_rls(session: AsyncSession) -> None:
    await session.execute(text("RESET app.user_id"))
    await session.execute(text("RESET app.tenant_id"))
    await session.execute(text("RESET app.cabinet_id"))
