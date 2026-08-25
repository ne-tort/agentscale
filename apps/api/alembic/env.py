"""Alembic migration environment."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from prodavan.config.settings import settings
from prodavan.infrastructure.persistence.models import Base  # noqa: F401 — register metadata
from prodavan.infrastructure.persistence.models.base import Base as _Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = _Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Bootstrap table + revision-managed indexes stay out of ORM drift checks."""
    if type_ == "table" and name == "stub_meta":
        return False
    if type_ in ("index", "unique_constraint"):
        return False
    return True


def _configure_context(connection=None, url=None, **kwargs) -> None:
    opts = {
        "target_metadata": target_metadata,
        "include_object": include_object,
        # Indexes/uniques often live in explicit revisions, not declarative Index().
        "compare_indexes": False,
        "compare_unique_constraints": False,
    }
    if connection is not None:
        opts["connection"] = connection
    if url is not None:
        opts["url"] = url
    opts.update(kwargs)
    context.configure(**opts)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    _configure_context(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure_context(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
