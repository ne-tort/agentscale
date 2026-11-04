"""Live PostgreSQL proof for ``SqlAuthSessionOwnership``.

Everything else in this package is a unit test: the builders compose and validate, and the
ownership port refuses a blank triple before any SQL.  What cannot be established without a
server is the *predicate*: that a stored ``auth_sessions`` row plus a live employee and an
active membership really answer ``True``, and that each way of breaking that chain (revoked,
expired, wrong tenant, wrong subject, wrong session, inactive employee) answers ``False``
rather than raising or guessing.

The suite is skipped unless ``PRODAVAN_TEST_DATABASE_URL`` points at a disposable database
(the container in the task brief is one):

    docker run -d --name pg-test -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test \
        -e POSTGRES_DB=prodavan_test -p 55432:5432 postgres:16.15-alpine
    $env:PRODAVAN_TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/prodavan_test"

Each case builds a throwaway ``m05ctx_<hex>`` schema from the ORM tables, points
``search_path`` at it and drops it afterwards, so nothing in ``public`` is touched.  No
migration is applied and no relation outside the three the predicate reads is created.
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

from prodavan.infrastructure.persistence.models.agent import AgentSessionRow  # noqa: F401
from prodavan.infrastructure.persistence.models.auth_sessions import AuthSessionRow
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.application.migration.m05_contexts import SqlAuthSessionOwnership

from . import fixtures as fx

DSN_ENV = "PRODAVAN_TEST_DATABASE_URL"
ASYNCPG = "postgresql+asyncpg"

#: Built from the models, so the schema under test is the declared one.
TABLES = (
    CompanyRow.__table__,
    EmployeeRow.__table__,
    MembershipRow.__table__,
    AuthSessionRow.__table__,
)


def _live_dsn() -> str:
    raw = (os.getenv(DSN_ENV) or "").strip()
    if not raw:
        pytest.skip(f"{DSN_ENV} is not set; live ownership proof is skipped")
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
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """One isolated schema with the four relations the ownership predicate reads."""

    dsn = _live_dsn()
    schema = f"m05ctx_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with admin.begin() as connection:
            await connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            dsn,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            async with engine.begin() as connection:
                for table in TABLES:
                    await connection.run_sync(table.create, checkfirst=True)
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()
    finally:
        async with admin.begin() as connection:
            await connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture()
def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    now: datetime,
    *,
    tenant: str = fx.TENANT,
    subject: str = fx.SUBJECT,
    session: str = fx.SESSION,
    employee_status: str = "active",
    employee_deleted: bool = False,
    revoked: bool = False,
    expired: bool = False,
    session_tenant: str | None = fx.TENANT,
    with_membership: bool = True,
    keycloak_sub: str | None = None,
) -> None:
    """One company, one employee, one membership and one auth-session row.

    The credential's lifetime is computed up front because
    ``ck_auth_sessions_expiry_after_issue`` requires ``expires_at > issued_at`` for *every*
    row: an "expired" fixture is a session that was issued two hours ago and expired one
    hour ago, not a row whose expiry was moved behind its issuance afterwards.
    """

    issued_at = now - timedelta(hours=2)
    expires_at = now - timedelta(hours=1) if expired else now + timedelta(hours=1)
    async with factory() as db:
        async with db.begin():
            db.add(CompanyRow(id=tenant, name=f"Company {tenant}", login_slug=tenant))
            db.add(
                EmployeeRow(
                    id=subject,
                    keycloak_sub=keycloak_sub if keycloak_sub is not None else f"sub-{subject}",
                    login=f"{subject}@{tenant}.local",
                    email=f"{subject}@{tenant}.local",
                    status=employee_status,
                    deleted_at=now - timedelta(days=1) if employee_deleted else None,
                )
            )
            if with_membership:
                db.add(
                    MembershipRow(
                        id=f"membership-{subject}",
                        company_id=tenant,
                        employee_id=subject,
                        role="member",
                    )
                )
            db.add(
                AuthSessionRow(
                    correlation_id=session,
                    subject_claim=f"sub-{subject}",
                    resolution_source="bearer_jwt_verified",
                    issued_at=issued_at,
                    expires_at=expires_at,
                    revoked_at=now - timedelta(minutes=5) if revoked else None,
                    company_id=session_tenant,
                )
            )


async def test_a_live_recorded_session_proves_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is True


async def test_a_session_of_another_tenant_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now, session_tenant=fx.OTHER_TENANT)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is False


async def test_a_session_selected_no_tenant_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    """``company_id IS NULL`` matches only a NULL assertion, and a caller never makes one."""

    await _seed(factory, now, session_tenant=None)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is False


async def test_a_revoked_session_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now, revoked=True)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is False


async def test_an_expired_session_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now, expired=True)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is False


async def test_an_inactive_employee_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now, employee_status="disabled")
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is False


async def test_a_deleted_employee_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now, employee_deleted=True)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is False


async def test_a_subject_without_a_membership_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now, with_membership=False)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
    ) is False


async def test_a_different_session_or_subject_is_not_ownership(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    await _seed(factory, now)
    port = SqlAuthSessionOwnership(session_factory=factory)

    assert await port.prove(
        tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id="session-someone-else"
    ) is False
    assert await port.prove(
        tenant_id=fx.TENANT, subject_id="employee-someone-else", session_id=fx.SESSION
    ) is False


async def test_the_proof_is_a_read_only_query(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    """``prove`` must never write: it is called once per imported row."""

    await _seed(factory, now)
    port = SqlAuthSessionOwnership(session_factory=factory)

    async with factory() as db:
        before = (
            await db.execute(sa.text("SELECT count(*) FROM auth_sessions"))
        ).scalar_one()

    for _ in range(3):
        assert await port.prove(
            tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION
        ) is True

    async with factory() as db:
        after = (await db.execute(sa.text("SELECT count(*) FROM auth_sessions"))).scalar_one()
    assert before == after == 1


async def test_a_missing_relation_raises_rather_than_answering_false(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """An unreadable store is an outage, not a revocation.

    This is the deliberate difference from every negative answer above: a ``False`` for a
    broken connection would report "session revoked" for a database problem, which is exactly
    the conflation ``principal_resolver`` refuses.  The stage reports the exception class and
    commits nothing.
    """

    port = SqlAuthSessionOwnership(session_factory=factory)

    async with factory() as db:
        async with db.begin():
            await db.execute(sa.text("DROP TABLE auth_sessions"))

    with pytest.raises(sa.exc.DBAPIError):
        await port.prove(tenant_id=fx.TENANT, subject_id=fx.SUBJECT, session_id=fx.SESSION)
