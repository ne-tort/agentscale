"""Real-PostgreSQL acceptance harness for the plugin platform chain.

This package is **test-only**. It never reads the application DSN
(``settings.database_url`` / ``DATABASE_URL``); the only accepted inputs are
explicit disposable credentials:

* ``PRODAVAN_PLUGIN_TEST_ADMIN_DSN`` — a superuser/CREATEDB connection to a
  throwaway PostgreSQL server (e.g. ``postgres:16.15-alpine`` container);
* ``PRODAVAN_PLUGIN_TEST_DSN`` — required base DSN on the same server whose
  database name is **replaced** per test with a fresh random name.

Every test runs against its own database ``plugin_pg_<20 hex>`` created through
the admin connection and dropped in teardown. When the env is absent the whole
directory skips locally; an acceptance run with the env set must report
``collected > 0`` and ``skipped == 0`` (see ``README.md``).

Alembic ``env.py`` resolves its URL from the ``settings`` singleton, so all
migration commands are invoked in a worker thread with the settings URL
patched to the disposable database DSN only.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import socket
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

ADMIN_DSN_ENV = "PRODAVAN_PLUGIN_TEST_ADMIN_DSN"
"""Privileged DSN to a disposable server (database ``postgres`` or similar)."""

BASE_DSN_ENV = "PRODAVAN_PLUGIN_TEST_DSN"
"""Required non-privileged DSN; only its database name is replaced per test."""

DB_PREFIX = "plugin_pg"
"""Every database this suite creates carries this prefix and is always dropped."""

ASYNC_PG_DRIVER = "postgresql+asyncpg"

API_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_SCRIPT_LOCATION = API_ROOT / "alembic"
ALEMBIC_SRC_PATH = API_ROOT / "src"

PENDING_0802 = "2026110802"
HEAD_REVISION = "2026110909"
STAGE_PRE_SCOPE = "2026110908"
STAGE_ROLLBACK = "2026110907"
STAGE_PLATFORM = "2026110801"
pytestmark = pytest.mark.integration


def _as_asyncpg_url(raw: str, *, env: str) -> str:
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(f"{env} must be a PostgreSQL DSN, got {url.drivername!r}")
    if url.drivername != ASYNC_PG_DRIVER:
        url = url.set(drivername=ASYNC_PG_DRIVER)
    if not url.database:
        raise pytest.UsageError(f"{env} must name a database")
    return url.render_as_string(hide_password=False)


def _tcp_reachable(url: str, *, timeout: float = 1.5) -> bool:
    parsed = make_url(url)
    host = parsed.host or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@contextlib.asynccontextmanager
async def engine_for(url: str) -> AsyncIterator[AsyncEngine]:
    # Mirror production infrastructure/persistence/database.py. The default
    # QueuePool deliberately retains connections so leaked locks cannot hide.
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def pg_admin_dsn() -> str:
    raw = (os.getenv(ADMIN_DSN_ENV) or "").strip()
    if not raw:
        if (os.getenv(BASE_DSN_ENV) or "").strip():
            raise pytest.UsageError(f"{BASE_DSN_ENV} requires {ADMIN_DSN_ENV}")
        pytest.skip(
            f"{ADMIN_DSN_ENV} is not set. This suite only runs against a disposable "
            "PostgreSQL server; see tests/migration/plugin_postgres/README.md."
        )
    url = _as_asyncpg_url(raw, env=ADMIN_DSN_ENV)
    if not _tcp_reachable(url):
        raise RuntimeError(
            f"Disposable PostgreSQL is not reachable at "
            f"{make_url(url).render_as_string(hide_password=True)} ({ADMIN_DSN_ENV}); "
            "an explicitly configured acceptance run must not skip"
        )
    return url


@pytest.fixture(scope="session")
def pg_base_dsn(pg_admin_dsn: str) -> str:
    """The DSN used for disposable test databases (non-admin by default)."""
    raw = (os.getenv(BASE_DSN_ENV) or "").strip()
    if not raw:
        raise pytest.UsageError(
            f"{BASE_DSN_ENV} must be set explicitly together with {ADMIN_DSN_ENV}; "
            "no application/shared DSN fallback is allowed"
        )
    url = _as_asyncpg_url(raw, env=BASE_DSN_ENV)
    if not _tcp_reachable(url):
        raise RuntimeError(
            f"{BASE_DSN_ENV} is set but nothing listens at "
            f"{make_url(url).render_as_string(hide_password=True)}; "
            "an explicitly configured acceptance run must not skip"
        )
    return url


# --------------------------------------------------------------------------- #
# alembic plumbing (synchronous; always called in a worker thread)
# --------------------------------------------------------------------------- #
def alembic_config() -> Config:
    """A ``Config`` with no ini file (never reconfigures pytest logging)."""
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("prepend_sys_path", str(ALEMBIC_SRC_PATH))
    config.set_main_option("version_path_separator", "os")
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None, "the alembic script directory has no head revision"
    return head


def assert_no_pending_0802_in_chain() -> None:
    """The dormant 0802 inventory must never become an active revision."""
    script = ScriptDirectory.from_config(alembic_config())
    chain: list[str] = []
    current: str | None = script.get_current_head()
    while current is not None:
        assert current != PENDING_0802, (
            "pending 2026110802 leaked into the active revision chain; "
            "this harness must never apply it"
        )
        revision = script.get_revision(current)
        assert revision is not None
        parent = revision.down_revision
        assert not isinstance(parent, tuple), f"{current!r} is a merge point"
        chain.append(current)
        current = parent
    assert {HEAD_REVISION, STAGE_PRE_SCOPE, STAGE_ROLLBACK, STAGE_PLATFORM} <= set(chain)


def _assert_alembic_chain_is_safe() -> None:
    """Validate the complete active plan before fresh and historical upgrades."""

    actual_head = head_revision()
    assert actual_head == HEAD_REVISION, (
        f"unexpected active alembic head {actual_head!r}; expected {HEAD_REVISION!r}"
    )
    assert_no_pending_0802_in_chain()


def _invoke(dsn: str, operation: str, revision: str) -> None:
    # This seam covers every direct upgrade caller, including 0801/0907 setup,
    # repeated head upgrades, artifact races, rollback generations and locks.
    _assert_alembic_chain_is_safe()
    from prodavan.config.settings import settings

    config = alembic_config()
    with patch.object(settings, "database_url", dsn):
        if operation == "upgrade":
            command.upgrade(config, revision)
        elif operation == "check":
            command.check(config)
        else:  # pragma: no cover - programming error
            raise ValueError(f"unknown alembic operation {operation!r}")


async def alembic_run(dsn: str, operation: str, revision: str = "head") -> None:
    """Run one alembic command off the event loop (env calls ``asyncio.run``)."""
    await asyncio.to_thread(_invoke, dsn, operation, revision)


# --------------------------------------------------------------------------- #
# disposable database lifecycle
# --------------------------------------------------------------------------- #
class DisposableDatabase:
    """One random database plus its engines; always dropped in teardown."""

    def __init__(self, name: str, dsn: str) -> None:
        self.name = name
        self.dsn = dsn

    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(engine, expire_on_commit=False)

    async def scalar(self, sql: str, params: dict | None = None):
        async with engine_for(self.dsn) as engine:
            async with engine.connect() as conn:
                return await conn.scalar(text(sql), params or {})

    async def rows(self, sql: str, params: dict | None = None):
        async with engine_for(self.dsn) as engine:
            async with engine.connect() as conn:
                return list((await conn.execute(text(sql), params or {})).all())

    async def execute(self, sql: str, params: dict | None = None) -> None:
        async with engine_for(self.dsn) as engine:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params or {})

    async def tables(self, schema: str = "public") -> set[str]:
        rows = await self.rows(
            "SELECT tablename FROM pg_tables WHERE schemaname = :schema",
            {"schema": schema},
        )
        return {row[0] for row in rows}

    async def stamped_revision(self) -> str | None:
        async with engine_for(self.dsn) as engine:
            async with engine.connect() as conn:
                versioned = await conn.scalar(
                    text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
                )
                if not versioned:
                    return None
                return await conn.scalar(text("SELECT version_num FROM alembic_version"))


async def _assert_clean_database(conn: AsyncConnection, name: str) -> None:
    tables = set(
        (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
        .scalars()
        .all()
    )
    versioned = await conn.scalar(
        text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    )
    assert not versioned and not tables, (
        f"disposable database {name!r} is not empty; refusing to run migrations against it"
    )


def _generated_identifier(name: str) -> str:
    assert re.fullmatch(r"plugin_pg_[0-9a-f]{20}", name), name
    return f'"{name}"'


async def _drop_database(admin_dsn: str, name: str) -> None:
    async with engine_for(admin_dsn) as admin:
        async with admin.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            await conn.execute(text(f"DROP DATABASE IF EXISTS {_generated_identifier(name)}"))


@pytest_asyncio.fixture
async def pg_disposable_db(pg_admin_dsn: str, pg_base_dsn: str) -> AsyncIterator[DisposableDatabase]:
    """A fresh random database per test; alembic runs inside it, then it is dropped."""
    name = f"{DB_PREFIX}_{uuid.uuid4().hex[:20]}"
    admin_url = make_url(pg_admin_dsn)
    base_url = make_url(pg_base_dsn)
    assert base_url.host == admin_url.host and (base_url.port or 5432) == (admin_url.port or 5432), (
        f"{BASE_DSN_ENV} must point at the same disposable server as {ADMIN_DSN_ENV}"
    )
    owner = base_url.username or admin_url.username
    assert owner and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", owner), (
        "the disposable database owner must be a simple PostgreSQL role name"
    )
    async with engine_for(pg_admin_dsn) as admin:
        async with admin.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(f'CREATE DATABASE {_generated_identifier(name)} OWNER "{owner}"')
            )
    dsn = base_url.set(database=name).render_as_string(hide_password=False)
    db = DisposableDatabase(name=name, dsn=dsn)
    try:
        async with engine_for(dsn) as probe:
            async with probe.connect() as conn:
                await _assert_clean_database(conn, name)
        yield db
    finally:
        await _drop_database(pg_admin_dsn, name)


# --------------------------------------------------------------------------- #
# collection guards
# --------------------------------------------------------------------------- #
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "plugin_postgres: disposable real-PostgreSQL plugin acceptance (never SQLite)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    package = Path(__file__).resolve().parent
    for item in items:
        path = Path(str(item.fspath)).resolve()
        if package not in path.parents:
            continue
        assert item.get_closest_marker("integration") is not None, item.nodeid
        assert item.get_closest_marker("plugin_postgres") is not None, item.nodeid
        assert item.get_closest_marker("skip") is None, item.nodeid
        assert item.get_closest_marker("skipif") is None, item.nodeid
