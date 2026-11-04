"""PostgreSQL integration harness for the migration concurrency suite (MIG-INT-PG-01).

This package is authored under the migration **verification freeze**: nothing
here is executed while the freeze is in force, and importing this module must
therefore never open a connection, run a migration or touch a database.  Every
side effect lives inside a fixture, so collection is inert.

Design of the harness
---------------------
* **Connection comes from the environment.**  ``PRODAVAN_TEST_DATABASE_URL`` is
  the only accepted source; there is no default, because defaulting to a
  developer's ``DATABASE_URL`` is how a concurrency suite ends up truncating
  somebody's working database.  Missing variable or unreachable server means the
  whole directory *skips* with an actionable reason instead of failing.
* **Isolation is a schema per test.**  Each test creates a fresh
  ``pgint_<hex>`` schema, points ``search_path`` at it on *every* connection and
  builds the schema from ``Base.metadata`` (models are the schema source of
  truth per ``docs/07-infrastructure/alembic.md``).  The schema is dropped in
  teardown.  A shared ``public`` schema is never written to, so the suite is
  safe to point at a database that also holds the migration-cycle artefacts and
  at a database that several runs share.
* **No row-level security.**  ``create_all`` installs no policies; RLS is
  covered by ``tests/migration/rls``.  Keeping policies out of this suite is
  deliberate: a policy that filters by tenant would turn a concurrency
  assertion into a policy assertion.
* **Determinism.**  Races are created by holding real transactions open on
  independent connections and released through PostgreSQL advisory locks plus
  ``asyncio`` events (:mod:`_support`), never by ``sleep``.  Where a test needs
  to observe that a peer backend is *actually* waiting, it polls
  ``pg_stat_activity`` for a bound condition rather than sleeping.

The skipped ``test_alembic_cycle`` test needs a *database*, not a schema (the
alembic environment resolves ``settings.database_url`` and always works in the
first schema of the connection's ``search_path``); see that module.
"""

from __future__ import annotations

import contextlib
import os
import socket
import uuid
import warnings
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import Row, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from prodavan.infrastructure.persistence.models import Base

TEST_DATABASE_URL_ENV = "PRODAVAN_TEST_DATABASE_URL"
"""The one accepted source of the PostgreSQL DSN."""

MIGRATION_DATABASE_URL_ENV = "PRODAVAN_TEST_MIGRATION_DATABASE_URL"
"""Optional dedicated (empty) database for the alembic upgrade/downgrade cycle."""

SCHEMA_PREFIX = "pgint"
"""Every schema this suite creates is prefixed so a leak is recognisable."""

ASYNC_PG_DRIVER = "postgresql+asyncpg"


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #
def _as_asyncpg_url(raw: str) -> str:
    """Normalise a PostgreSQL DSN to the asyncpg driver.

    ``postgresql://`` and ``postgres://`` are accepted because that is what
    people copy from ``docker run`` output; anything else is a configuration
    error and must fail loudly rather than silently test nothing.
    """
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(
            f"{TEST_DATABASE_URL_ENV} must be a PostgreSQL DSN, got driver {url.drivername!r}"
        )
    if url.drivername != ASYNC_PG_DRIVER:
        url = url.set(drivername=ASYNC_PG_DRIVER)
    if not url.database:
        raise pytest.UsageError(f"{TEST_DATABASE_URL_ENV} must name a database")
    return url.render_as_string(hide_password=False)


def _tcp_reachable(url: str, *, timeout: float = 1.0) -> bool:
    """TCP reachability only.

    Deliberately not an ``asyncio.run()`` probe: the repository already hit
    Windows Proactor problems with a nested loop in ``tests/conftest.py``, and a
    plain socket check cannot interact with the test's event loop at all.
    """
    parsed = make_url(url)
    host = parsed.host or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def database_url_from_env() -> str | None:
    """Return the normalised test DSN, or ``None`` when it is not configured."""
    raw = (os.getenv(TEST_DATABASE_URL_ENV) or "").strip()
    if not raw:
        return None
    return _as_asyncpg_url(raw)


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """The PostgreSQL DSN under test; skips the directory when unavailable."""
    url = database_url_from_env()
    if url is None:
        pytest.skip(
            f"{TEST_DATABASE_URL_ENV} is not set. This suite only runs against a real "
            "PostgreSQL; see tests/migration/postgres_integration/README.md."
        )
    if not _tcp_reachable(url):
        pytest.skip(
            f"PostgreSQL is not reachable at "
            f"{make_url(url).render_as_string(hide_password=True)} "
            f"({TEST_DATABASE_URL_ENV} is set but nothing listens on that endpoint)."
        )
    return url


# --------------------------------------------------------------------------- #
# engine helpers
# --------------------------------------------------------------------------- #
@contextlib.asynccontextmanager
async def engine_for(
    url: str,
    *,
    search_path: str | None = None,
) -> AsyncIterator[AsyncEngine]:
    """Open a ``NullPool`` engine, optionally pinned to one PostgreSQL schema.

    ``NullPool`` is required, not cosmetic: every concurrency assertion in this
    suite needs *independent* connections, and it also guarantees that a
    ``search_path`` set through ``server_settings`` is applied to each new
    connection instead of being inherited from a pooled predecessor.
    """
    connect_args = (
        {"server_settings": {"search_path": search_path}} if search_path is not None else {}
    )
    engine = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    try:
        yield engine
    finally:
        await engine.dispose()


def _create_metadata_tables(connection: Connection) -> None:
    """Create every ORM table (``checkfirst=False`` skips the existence probe).

    ``checkfirst=True`` would look for the tables through ``search_path`` and
    could therefore conclude "already there" because of a ``public`` copy,
    silently binding the test to the wrong schema.  Unqualified ``CREATE TABLE``
    and ``REFERENCES`` always resolve to the first schema on ``search_path``,
    which is the isolated one.
    """
    Base.metadata.create_all(connection, checkfirst=False)


async def _assert_schema_owns_tables(connection: AsyncConnection, schema: str) -> None:
    """Fail closed when the ORM tables did not land in the isolated schema."""
    found = set(
        (
            await connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"),
                {"schema": schema},
            )
        )
        .scalars()
        .all()
    )
    missing = sorted(set(Base.metadata.tables) - found)
    assert not missing, (
        f"ORM tables did not land in the isolated schema {schema!r}; "
        f"a schema on search_path shadowed create_all. Missing (first 10): {missing[:10]}"
    )


# --------------------------------------------------------------------------- #
# schema harness
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SchemaHarness:
    """One isolated schema plus the helpers the tests need from it."""

    name: str
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    admin_engine: AsyncEngine

    # -------------------------------------------------------------- reading
    async def scalar(self, sql: str, params: dict | None = None):
        async with self.engine.connect() as conn:
            return await conn.scalar(text(sql), params or {})

    async def rows(self, sql: str, params: dict | None = None) -> list[Row]:
        async with self.engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())

    async def count(self, table: str) -> int:
        """Row count of one table in this schema (table name is not user input)."""
        value = await self.scalar(f'SELECT count(*) FROM "{table}"')
        return int(value or 0)

    async def counts(self, *tables: str) -> dict[str, int]:
        return {table: await self.count(table) for table in tables}

    # -------------------------------------------------------------- writing
    async def execute(self, sql: str, params: dict | None = None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text(sql), params or {})

    @contextlib.asynccontextmanager
    async def transaction(
        self, *, commit: bool = False
    ) -> AsyncIterator[AsyncConnection]:
        """Hold a real transaction open on its own connection.

        ``commit=False`` (default) rolls back, which is what a test that only
        wants to *occupy* a row lock needs.  Pass ``commit=True`` when the
        transaction is meant to persist.
        """
        async with self.engine.connect() as conn:
            trans = await conn.begin()
            try:
                yield conn
            finally:
                if trans.is_active:
                    if commit:
                        await trans.commit()
                    else:
                        await trans.rollback()

    # -------------------------------------------------------------- sessions
    def session(self) -> AsyncSession:
        """A session on the default (auto-begin) transaction discipline."""
        return self.session_factory()

    def repository(self):
        """The durable agent control-plane repository bound to this schema."""
        from prodavan.infrastructure.persistence.agent_control_plane.repository import (
            SqlAlchemyAgentControlPlaneRepository,
        )

        return SqlAlchemyAgentControlPlaneRepository(session_factory=self.session_factory)


async def _drop_schema(admin: AsyncEngine, name: str) -> None:
    """Drop the isolated schema, tolerating a leaked connection.

    A leftover ``pgint_*`` schema is noise, while an exception raised *here*
    would replace the real assertion failure with a confusing teardown error,
    so the failure is reported as a warning instead of being re-raised.
    """
    try:
        async with admin.begin() as conn:
            await conn.execute(text("SET lock_timeout = '5s'"))
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
    except SQLAlchemyError as exc:  # pragma: no cover - only on a leaked session
        warnings.warn(
            f"could not drop isolated schema {name!r}: {exc}. "
            "Drop it manually; a leaked connection is the usual cause.",
            stacklevel=2,
        )


@pytest_asyncio.fixture
async def pg_schema(pg_dsn: str) -> AsyncIterator[SchemaHarness]:
    """A fresh PostgreSQL schema built from the ORM metadata, dropped afterwards."""
    name = f"{SCHEMA_PREFIX}_{uuid.uuid4().hex[:12]}"
    async with engine_for(pg_dsn) as admin:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{name}"'))
        try:
            async with engine_for(pg_dsn, search_path=name) as engine:
                async with engine.begin() as conn:
                    await conn.run_sync(_create_metadata_tables)
                    await _assert_schema_owns_tables(conn, name)
                yield SchemaHarness(
                    name=name,
                    engine=engine,
                    session_factory=async_sessionmaker(engine, expire_on_commit=False),
                    admin_engine=admin,
                )
        finally:
            await _drop_schema(admin, name)


__all__ = [
    "ASYNC_PG_DRIVER",
    "MIGRATION_DATABASE_URL_ENV",
    "SCHEMA_PREFIX",
    "SchemaHarness",
    "TEST_DATABASE_URL_ENV",
    "database_url_from_env",
    "engine_for",
    "pg_dsn",
    "pg_schema",
]
