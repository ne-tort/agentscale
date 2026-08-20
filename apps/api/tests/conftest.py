"""Pytest fixtures for integration tests."""

import os
import uuid

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


class _StubS4B:
    """Tests never hit live S4B; ping fails unless a test replaces the gateway."""

    def ping(self, username: str, password: str) -> dict:
        return {"ok": False, "error_code": "auth_failed", "error": "stub: no live S4B"}

    def search_by_part_numbers(self, username: str, password: str, part_numbers: list[str]) -> dict:
        return {"ok": False, "error_code": "auth_failed", "error": "stub: no live S4B"}


@pytest.fixture(autouse=True)
def stub_s4b_gateway(monkeypatch):
    monkeypatch.setattr(
        "prodavan.application.integrations.s4b_runtime._gateway",
        _StubS4B(),
    )


@pytest_asyncio.fixture(autouse=True)
async def seed_platform_admin():
    """Ensure bootstrap platform.admin exists (ASGI lifespan may not run in all clients)."""
    from prodavan.application.services.auth_service import ensure_platform_admin
    from prodavan.infrastructure.persistence.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        await ensure_platform_admin(session)
    yield


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


@pytest.fixture
def unique_suffix() -> str:
    return uuid.uuid4().hex[:8]
