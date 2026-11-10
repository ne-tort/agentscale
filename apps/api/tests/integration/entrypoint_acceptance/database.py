"""Disposable real PostgreSQL lifecycle shared by both entrypoint subprocesses."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.migration.plugin_postgres.conftest import (
    DisposableDatabase,
    _assert_clean_database,
)

from .config import AcceptanceConfig

_NAME = re.compile(r"plugin_pg_[0-9a-f]{20}")


def _sqlalchemy_dsn(url: str) -> str:
    """Translate libpq sslmode into asyncpg ssl for SQLAlchemy engines only.

    The harness contract keeps sslmode in ENTRYPOINT DSNs for the tenant-admin
    adapter, while SQLAlchemy/asyncpg requires the equivalent ssl parameter.
    """
    parsed = make_url(url)
    query = dict(parsed.query)
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    return parsed.set(query=query).render_as_string(hide_password=False)


def _engine_for(url: str) -> Any:
    return create_async_engine(_sqlalchemy_dsn(url), echo=False, pool_pre_ping=True)


async def _drop_database(admin_dsn: str, name: str) -> None:
    async with _engine_for(admin_dsn) as admin:
        async with admin.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))


async def _alembic_run(dsn: str, operation: str, revision: str = "head") -> None:
    from tests.migration.plugin_postgres.conftest import alembic_run

    await alembic_run(_sqlalchemy_dsn(dsn), operation, revision)


@dataclass(slots=True)
class DisposablePostgres:
    admin_dsn: str
    database: Any
    engine: AsyncEngine | None = None

    @classmethod
    async def open(cls, config: AcceptanceConfig) -> "DisposablePostgres":
        admin = make_url(config.postgres_admin_dsn)
        base = make_url(config.postgres_dsn)
        for name, url in (("POSTGRES_ADMIN_DSN", admin), ("POSTGRES_DSN", base)):
            if url.get_backend_name() != "postgresql" or not url.database:
                raise RuntimeError(f"{name} must be a PostgreSQL DSN with a database")
        if (
            admin.host != base.host
            or (admin.port or 5432) != (base.port or 5432)
        ):
            raise RuntimeError("both PostgreSQL DSNs must name the same disposable server")
        owner = base.username or admin.username
        if not owner or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", owner) is None:
            raise RuntimeError("disposable PostgreSQL owner must be a simple role name")
        admin_dsn = _sqlalchemy_dsn(admin.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False))
        base = base.set(drivername="postgresql+asyncpg")
        name = f"plugin_pg_{uuid.uuid4().hex[:20]}"
        assert _NAME.fullmatch(name) is not None
        async with _engine_for(admin_dsn) as admin_engine:
            async with admin_engine.connect() as conn:
                conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text(f'CREATE DATABASE "{name}" OWNER "{owner}"'))
        dsn = _sqlalchemy_dsn(base.set(database=name).render_as_string(hide_password=False))
        # Reuse the existing real-Postgres disposable database owner/cleanup.
        from tests.migration.plugin_postgres.conftest import DisposableDatabase

        result = cls(admin_dsn=admin_dsn, database=DisposableDatabase(name=name, dsn=dsn))
        try:
            async with _engine_for(dsn) as probe:
                async with probe.connect() as conn:
                    await _assert_clean_database(conn, name)
            await _alembic_run(dsn, "upgrade", "head")
            result.engine = _engine_for(dsn)
            async with result.engine.connect() as conn:
                assert await conn.scalar(text("SELECT 1")) == 1
            return result
        except Exception:
            await _drop_database(admin_dsn, name)
            raise

    @property
    def dsn(self) -> str:
        return self.database.dsn

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        assert self.engine is not None
        return async_sessionmaker(self.engine, expire_on_commit=False)

    async def aclose(self) -> None:
        try:
            if self.engine is not None:
                await self.engine.dispose()
        finally:
            await _drop_database(self.admin_dsn, self.database.name)
