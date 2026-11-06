"""Live PostgreSQL acceptance for ``auth_sessions`` (skips without a DSN).

The rest of this package runs on SQLite, which proves the *decisions* but not the *types*.
This module closes that gap against a real server:

* the ``TEXT``/``TIMESTAMPTZ`` columns, the primary key, the sweep index and every named
  CHECK as the catalog reports them — compared with the ORM declaration, so a revision that
  drifted from the model cannot pass;
* a stored ``TIMESTAMPTZ`` comes back **timezone-aware** and is compared with the credential's
  expiry as an instant (the SQLite harness has to normalise that away; here it must not need
  to);
* the write path really is idempotent under the primary key, and the CHECK really refuses a
  request-derived source at the storage layer;
* the table really carries **no** policy and no RLS flag — the platform-anchor decision is a
  property of the deployed schema, not only of ``policies.py``.

Connection comes from ``PRODAVAN_TEST_DATABASE_URL`` (the repository's one accepted source
for live PostgreSQL tests, see ``tests/migration/postgres_integration/conftest.py``). Each
case creates a throwaway ``authsess_<hex>`` schema, points ``search_path`` at it and drops it
afterwards, so nothing in ``public`` is touched.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from prodavan.application.auth.auth_sessions import (
    AuthSessionWriteOutcome,
    record_auth_session,
)
from prodavan.application.migration.principal_resolver import (
    PrincipalRefusal,
    PrincipalRefusalRecord,
    SqlAuthSessionCredentialSource,
)
from prodavan.infrastructure.persistence.models.auth_sessions import AuthSessionRow

from .acceptance_fixtures import (
    CORRELATION,
    SUBJECT_A,
    TABLE,
    check_catalog,
    credential,
)

DSN_ENV = "PRODAVAN_TEST_DATABASE_URL"
ASYNCPG = "postgresql+asyncpg"


def _live_dsn() -> str:
    raw = (os.getenv(DSN_ENV) or "").strip()
    if not raw:
        pytest.skip(
            f"{DSN_ENV} is not set; live PostgreSQL acceptance for auth_sessions is skipped"
        )
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        pytest.skip(f"{DSN_ENV} is not a PostgreSQL DSN")
    if url.drivername != ASYNCPG:
        url = url.set(drivername=ASYNCPG)
    try:
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=1.0):
            pass
    except OSError:
        pytest.skip(f"nothing listens at {url.render_as_string(hide_password=True)}")
    return url.render_as_string(hide_password=False)


@pytest_asyncio.fixture()
async def pg_schema() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """One isolated schema containing only ``auth_sessions``, created from the model."""

    dsn = _live_dsn()
    name = f"authsess_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with admin.begin() as connection:
            await connection.execute(sa.text(f'CREATE SCHEMA "{name}"'))
        engine = create_async_engine(
            dsn,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": name}},
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(AuthSessionRow.__table__.create)
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()
    finally:
        async with admin.begin() as connection:
            await connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        await admin.dispose()


@pytest.fixture()
def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


async def test_the_deployed_catalog_matches_the_model(pg_schema, now: datetime) -> None:
    """Column names, types and nullability are what the model declares — on the server."""

    async with pg_schema() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = :table AND table_schema = current_schema() "
                    "ORDER BY ordinal_position"
                ),
                {"table": TABLE},
            )
        ).all()

    assert [(name, nullable) for name, _type, nullable in rows] == [
        (column.name, "YES" if column.nullable else "NO")
        for column in AuthSessionRow.__table__.columns
    ]
    types = {name: data_type for name, data_type, _nullable in rows}
    for name in ("correlation_id", "subject_claim", "resolution_source",
                 "company_id", "cabinet_id", "project_id"):
        assert types[name] == "text", name
    for name in ("issued_at", "expires_at", "revoked_at"):
        assert types[name] == "timestamp with time zone", name


async def test_the_deployed_checks_and_index_are_the_declared_ones(pg_schema) -> None:
    async with pg_schema() as session:
        checks = {
            name: " ".join(definition.split())
            for name, definition in (
                await session.execute(
                    sa.text(
                        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = to_regclass(:table) AND contype = 'c'"
                    ),
                    {"table": TABLE},
                )
            ).all()
        }
        indexes = set(
            (
                await session.execute(
                    sa.text("SELECT indexname FROM pg_indexes WHERE tablename = :table"),
                    {"table": TABLE},
                )
            )
            .scalars()
            .all()
        )
        primary = (
            await session.execute(
                sa.text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = to_regclass(:table) AND i.indisprimary"
                ),
                {"table": TABLE},
            )
        ).scalars().all()

    # Every declared CHECK reached the server, by name.
    assert set(checks) == set(check_catalog(AuthSessionRow.__table__))
    # The allow-list is expanded to its values by PostgreSQL; the values are the vocabulary.
    allow = checks["ck_auth_sessions_resolution_source"]
    for source in ("session_cookie", "bearer_jwt_verified", "internal_service_mtls",
                   "migration_operator"):
        assert source in allow
    assert "request_claim" not in allow

    assert "ix_auth_sessions_subject_expiry" in indexes
    assert tuple(primary) == ("correlation_id",)


async def test_no_policy_and_no_rls_flag_on_the_deployed_table(pg_schema) -> None:
    """The platform-anchor decision must hold in the deployed schema, not only in the registry."""

    async with pg_schema() as session:
        enabled = (
            await session.execute(
                sa.text(
                    "SELECT relrowsecurity FROM pg_class WHERE relname = :table "
                    "AND relnamespace = current_schema()::regnamespace"
                ),
                {"table": TABLE},
            )
        ).scalar_one()
        policies = (
            await session.execute(
                sa.text("SELECT count(*) FROM pg_policies WHERE tablename = :table"),
                {"table": TABLE},
            )
        ).scalar_one()

    assert enabled is False
    assert policies == 0


async def test_a_stored_expiry_comes_back_timezone_aware(pg_schema, now: datetime) -> None:
    """The reader must not need the SQLite timezone normalisation on a real server."""

    class Recorder:
        def __init__(self) -> None:
            self.records: list[PrincipalRefusalRecord] = []

        def record(self, record: PrincipalRefusalRecord) -> None:
            self.records.append(record)

    await record_auth_session(
        credential(expires_at=now + timedelta(minutes=20)),
        session_factory=pg_schema,
        clock=lambda: now,
    )

    recorder = Recorder()
    source = SqlAuthSessionCredentialSource(
        session_factory=pg_schema, clock=lambda: now, refusals=recorder
    )
    stored = await source.lookup(correlation_id=CORRELATION)

    assert stored is not None
    assert stored.subject_claim == SUBJECT_A
    assert stored.expires_at.tzinfo is not None
    assert stored.expires_at == now + timedelta(minutes=20)
    assert recorder.records == []


async def test_the_primary_key_makes_a_second_insert_impossible(pg_schema, now: datetime) -> None:
    """Two writers racing on one correlation id: one row, and the loser replays."""

    binding = credential(expires_at=now + timedelta(minutes=20))
    first = await record_auth_session(binding, session_factory=pg_schema, clock=lambda: now)

    async with pg_schema() as session:
        with pytest.raises(Exception):  # IntegrityError: the pk refuses the duplicate
            await session.execute(
                AuthSessionRow.__table__.insert().values(
                    correlation_id=CORRELATION,
                    subject_claim=SUBJECT_A,
                    resolution_source="bearer_jwt_verified",
                    issued_at=now,
                    expires_at=now + timedelta(minutes=20),
                )
            )
            await session.commit()
        await session.rollback()

    second = await record_auth_session(binding, session_factory=pg_schema, clock=lambda: now)

    assert first is not None and first.outcome is AuthSessionWriteOutcome.RECORDED
    assert second is not None and second.outcome is AuthSessionWriteOutcome.REPLAYED


async def test_the_server_refuses_a_request_derived_source(pg_schema, now: datetime) -> None:
    async with pg_schema() as session:
        with pytest.raises(Exception, match="ck_auth_sessions_resolution_source"):
            await session.execute(
                AuthSessionRow.__table__.insert().values(
                    correlation_id=CORRELATION,
                    subject_claim=SUBJECT_A,
                    resolution_source="job_payload",
                    issued_at=now,
                    expires_at=now + timedelta(minutes=20),
                )
            )
            await session.commit()
        await session.rollback()


async def test_the_server_refuses_an_expiry_before_issuance(pg_schema, now: datetime) -> None:
    async with pg_schema() as session:
        with pytest.raises(Exception, match="ck_auth_sessions_expiry_after_issue"):
            await session.execute(
                AuthSessionRow.__table__.insert().values(
                    correlation_id=CORRELATION,
                    subject_claim=SUBJECT_A,
                    resolution_source="bearer_jwt_verified",
                    issued_at=now,
                    expires_at=now - timedelta(seconds=1),
                )
            )
            await session.commit()
        await session.rollback()


async def test_the_reader_refuses_an_expired_row_on_a_real_server(pg_schema, now: datetime) -> None:
    class Recorder:
        def __init__(self) -> None:
            self.records: list[PrincipalRefusalRecord] = []

        def record(self, record: PrincipalRefusalRecord) -> None:
            self.records.append(record)

    async with pg_schema() as session:
        await session.execute(
            AuthSessionRow.__table__.insert().values(
                correlation_id=CORRELATION,
                subject_claim=SUBJECT_A,
                resolution_source="bearer_jwt_verified",
                issued_at=now - timedelta(minutes=30),
                expires_at=now - timedelta(minutes=1),
            )
        )
        await session.commit()

    recorder = Recorder()
    source = SqlAuthSessionCredentialSource(
        session_factory=pg_schema, clock=lambda: now, refusals=recorder
    )

    assert await source.lookup(correlation_id=CORRELATION) is None
    assert [record.reason for record in recorder.records] == [PrincipalRefusal.CREDENTIAL_EXPIRED]
