"""Pytest fixtures — stub."""

import asyncio
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from prodavan.main import create_app

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan",
)


def _postgres_available() -> bool:
    """TCP reachability only — avoid asyncio.run() (breaks Windows Proactor + TestClient)."""
    raw = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(raw)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available at DATABASE_URL",
)


def _wipe_public_tables() -> None:
    """Truncate app tables in a worker thread so Windows Proactor stays intact."""

    async def _wipe() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            tables = [r[0] for r in rows]
            if tables:
                quoted = ", ".join(f'"{t}"' for t in tables)
                await conn.execute(text(f"TRUNCATE {quoted} CASCADE"))
        await engine.dispose()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_wipe())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_runner).result(timeout=60)


@pytest.fixture(autouse=True)
def clean_engine_cache():
    """Reset engine cache and wipe public tables before each test (shared Postgres)."""
    import prodavan.infrastructure.persistence.database as db

    db._engine = None
    db._session_factory = None
    if _postgres_available():
        try:
            _wipe_public_tables()
        except Exception:
            pass
    yield
    db._engine = None
    db._session_factory = None


@pytest_asyncio.fixture
async def async_client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
