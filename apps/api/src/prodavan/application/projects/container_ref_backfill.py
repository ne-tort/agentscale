"""Runtime helper — normalize legacy container_ref values (C-MATERIALIZE)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def backfill_object_ws_container_refs(session: AsyncSession) -> dict[str, Any]:
    """Rewrite ``local-ws:`` → ``object-ws:`` for remaining legacy rows.

    Prefer Alembic ``2026082317``; this helper is for ops / partial envs.
    """
    result = await session.execute(
        text(
            """
            UPDATE projects
            SET container_ref = 'object-ws:' || substr(container_ref, 10)
            WHERE container_ref LIKE 'local-ws:%'
            """
        )
    )
    await session.commit()
    return {"updated": int(result.rowcount or 0)}
