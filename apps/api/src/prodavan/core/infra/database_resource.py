"""Database engine dispose as LifespanResource."""

from __future__ import annotations

from prodavan.core.lifespan.resource import LifespanResource
from prodavan.infrastructure.persistence.database import dispose_engine, get_engine


class DatabaseEngineResource(LifespanResource):
    """Lazy SQLAlchemy engine; dispose on shutdown."""

    @property
    def name(self) -> str:
        return "database"

    async def startup(self) -> None:
        # Engine is created lazily on first use; touch so misconfig fails early in ready.
        get_engine()

    async def shutdown(self) -> None:
        await dispose_engine()

    async def health(self) -> bool | None:
        from sqlalchemy import text

        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
