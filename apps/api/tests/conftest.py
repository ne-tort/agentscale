"""Pytest fixtures — stub."""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from prodavan.infrastructure.persistence.database import reset_engine
from prodavan.main import create_app

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan",
)


def _postgres_available() -> bool:
    import asyncio

    async def _check() -> bool:
        try:
            engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            return True
        except Exception:
            return False

    return asyncio.run(_check())


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available at DATABASE_URL",
)


@pytest_asyncio.fixture(autouse=True)
async def clean_engine_cache():
    yield
    await reset_engine()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
