"""Live PostgreSQL fixtures for the RLS acceptance package (task ``MIG-RLS-LIVE-01``).

The verification freeze is lifted, so :mod:`test_live_policy_installation` runs
against a real PostgreSQL.  Two contracts are copied from
``tests/migration/postgres_integration`` rather than reinvented:

* **the DSN comes from the environment** — ``PRODAVAN_TEST_DATABASE_URL`` is the
  only accepted source (no default, because defaulting to somebody's
  ``DATABASE_URL`` is how a suite ends up inspecting the wrong database);
* **an absent or unreachable server skips with an actionable reason** instead of
  failing.  Importing this module opens no connection at all.

What it deliberately does *not* copy is the throwaway-schema isolation.  The
object under test is "what did ``alembic upgrade head`` install into the schema
the application actually uses", so the suite reads the first schema on the
connection's ``search_path`` (``public`` in every deployed environment) and issues
**read-only** catalog queries only.  Nothing here creates, drops or migrates
anything: a test run cannot leave a database in a different shape than it found.

The whole catalog is read once per session (:func:`policy_catalog`), so the
assertions cannot disagree with each other about what the database contained at
one instant.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import socket
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL_ENV: Final = "PRODAVAN_TEST_DATABASE_URL"
ASYNC_PG_DRIVER: Final = "postgresql+asyncpg"


# --------------------------------------------------------------------------- #
# connection
# --------------------------------------------------------------------------- #
def database_url_from_env() -> str | None:
    """The normalised asyncpg DSN, or ``None`` when the variable is unset.

    ``postgresql://``/``postgres://`` are accepted because that is what people
    copy from ``docker run`` output; any other backend is a configuration error
    and must fail loudly rather than silently test nothing.
    """

    raw = (os.getenv(TEST_DATABASE_URL_ENV) or "").strip()
    if not raw:
        return None
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


def tcp_reachable(url: str, *, timeout: float = 1.0) -> bool:
    """TCP reachability only — a plain socket, no event loop involved."""

    parsed = make_url(url)
    try:
        with socket.create_connection(
            (parsed.host or "localhost", parsed.port or 5432), timeout=timeout
        ):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# catalog snapshot
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class InstalledPolicy:
    """One row of ``pg_policy`` joined to the relation it belongs to."""

    name: str
    #: ``polcmd``: ``'r'`` for SELECT, ``'a'`` for INSERT, ``'*'`` for ALL.
    command: str
    #: ``pg_policies.qual`` (USING expression), as PostgreSQL renders it.
    using: str | None
    #: ``pg_policies.with_check`` (WITH CHECK expression), if the command has one.
    with_check: str | None


@dataclasses.dataclass(frozen=True)
class CatalogRelation:
    """One relation of the inspected schema, with its RLS state."""

    name: str
    #: ``pg_class.relkind``: ``'r'`` ordinary table, ``'p'`` partitioned table.
    kind: str
    row_security: bool
    force_row_security: bool
    policies: tuple[InstalledPolicy, ...]


@dataclasses.dataclass(frozen=True)
class PolicyCatalog:
    """Everything the live assertions need, read once."""

    schema: str
    database: str
    #: ``alembic_version.version_num`` of the inspected database, if it has one.
    alembic_version: str | None
    relations: dict[str, CatalogRelation]

    def policies_of(self, table: str) -> tuple[InstalledPolicy, ...]:
        relation = self.relations.get(table)
        return () if relation is None else relation.policies

    @property
    def all_policies(self) -> tuple[tuple[str, InstalledPolicy], ...]:
        return tuple(
            (name, policy)
            for name, relation in self.relations.items()
            for policy in relation.policies
        )


_RELATIONS_SQL = text(
    """
    SELECT c.relname,
           c.relkind,
           c.relrowsecurity,
           c.relforcerowsecurity
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema()
      AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
    """
)

_POLICIES_SQL = text(
    """
    SELECT p.polname,
           p.polcmd,
           pg_get_expr(p.polqual, p.polrelid) AS using_expression,
           pg_get_expr(p.polwithcheck, p.polrelid) AS with_check_expression
    FROM pg_policy AS p
    JOIN pg_class AS c ON c.oid = p.polrelid
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema()
      AND c.relname = :table
    ORDER BY p.polname
    """
)

_SCHEMA_SQL = text("SELECT current_schema(), current_database()")

_ALEMBIC_VERSION_SQL = text("SELECT version_num FROM alembic_version")


async def read_policy_catalog(dsn: str) -> PolicyCatalog:
    """Read the RLS-relevant catalog of the connection's first schema."""

    engine = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            schema, database = (
                await connection.execute(_SCHEMA_SQL)
            ).one()
            relations: dict[str, CatalogRelation] = {}
            for name, kind, row_security, force_row_security in (
                await connection.execute(_RELATIONS_SQL)
            ).all():
                policies = tuple(
                    InstalledPolicy(
                        name=policy_name,
                        command=command,
                        using=using_expression,
                        with_check=with_check_expression,
                    )
                    for policy_name, command, using_expression, with_check_expression in (
                        await connection.execute(_POLICIES_SQL, {"table": name})
                    ).all()
                )
                relations[name] = CatalogRelation(
                    name=name,
                    kind=kind,
                    row_security=bool(row_security),
                    force_row_security=bool(force_row_security),
                    policies=policies,
                )
            try:
                version = (await connection.execute(_ALEMBIC_VERSION_SQL)).scalar_one_or_none()
            except Exception:  # noqa: BLE001 - a missing alembic_version is data, not an error
                version = None
            return PolicyCatalog(
                schema=schema,
                database=database,
                alembic_version=version,
                relations=relations,
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def live_database_url() -> str:
    """The DSN of the migrated database under test; skips when unavailable."""

    url = database_url_from_env()
    if url is None:
        pytest.skip(
            f"{TEST_DATABASE_URL_ENV} is not set. The live RLS acceptance test only runs "
            "against a PostgreSQL that has the full migration chain applied; see "
            "tests/migration/postgres_integration/README.md for how to start one."
        )
    if not tcp_reachable(url):
        pytest.skip(
            f"PostgreSQL is not reachable at {make_url(url).render_as_string(hide_password=True)} "
            f"({TEST_DATABASE_URL_ENV} is set but nothing listens on that endpoint)."
        )
    return url


@pytest.fixture(scope="session")
def policy_catalog(live_database_url: str) -> PolicyCatalog:
    """The catalog snapshot every live assertion in this package shares.

    Synchronous on purpose: the assertions are plain (non-``async``) tests, and a
    private event loop keeps this independent of whichever loop mode
    ``pytest-asyncio`` is configured with - the same reasoning
    ``postgres_integration`` records for its own synchronous fixtures on Windows.
    """

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(read_policy_catalog(live_database_url))
    finally:
        loop.close()


__all__ = [
    "ASYNC_PG_DRIVER",
    "CatalogRelation",
    "InstalledPolicy",
    "PolicyCatalog",
    "TEST_DATABASE_URL_ENV",
    "database_url_from_env",
    "live_database_url",
    "policy_catalog",
    "read_policy_catalog",
    "tcp_reachable",
]
