"""Pytest fixtures — stub."""

import asyncio
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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


def sql_backdate_project(project_id: str, updated_at: datetime) -> None:
    """Set projects.updated_at/created_at for idle-pause sweep tests."""

    async def _run() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE projects SET updated_at = :ts, created_at = :ts WHERE id = :pid"),
                {"ts": updated_at, "pid": project_id},
            )
        await engine.dispose()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_runner).result(timeout=30)


def _run_async(coro_factory, *, timeout: float) -> None:
    """Run async work on a fresh event loop in a worker thread (Windows Proactor safe)."""

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro_factory())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_runner).result(timeout=timeout)


def _dispose_app_engine() -> None:
    """Release pooled DB connections so TRUNCATE cannot wait on locks forever."""
    import prodavan.infrastructure.persistence.database as db

    async def _dispose() -> None:
        await db.dispose_engine()

    _run_async(_dispose, timeout=30)


def _wipe_public_tables() -> None:
    """Truncate app tables in a worker thread so Windows Proactor stays intact."""

    async def _wipe() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            await conn.execute(text("SET lock_timeout = '5s'"))
            await conn.execute(text("SET statement_timeout = '30s'"))
            result = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            tables = [r[0] for r in result.all()]
            if tables:
                quoted = ", ".join(f'"{t}"' for t in tables)
                await conn.execute(text(f"TRUNCATE {quoted} CASCADE"))
        await engine.dispose()

    _run_async(_wipe, timeout=45)


@pytest.fixture(autouse=True)
def clean_engine_cache():
    """Dispose pooled connections and wipe public tables before each test."""
    _dispose_app_engine()
    if _postgres_available():
        _wipe_public_tables()
    yield
    _dispose_app_engine()


@pytest_asyncio.fixture
async def async_client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
