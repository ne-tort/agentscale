"""Real-PostgreSQL acceptance for the legacy gateway ports (opt-in, skips by default).

The SQLite suite proves query/decision behaviour; it cannot prove the *types*.
On PostgreSQL the two things this module exists for are different from the mirror:

* ``manifest``/``requested_capabilities``/``grants`` are ``JSONB``.  A read that
  came back as the wrong Python shape would pass on SQLite's ``JSON`` mirror and
  fail here.
* every timestamp is ``timestamptz``, so the "naive on SQLite" normalisation in
  the adapter must be a no-op — and the domain contracts refuse a naive value, so
  a regression would surface as a ``ValueError`` out of the port.

The suite runs only when ``PRODAVAN_TEST_DATABASE_URL`` names a reachable
database, uses a throwaway ``legacypg_<hex>`` schema built from the production
``Base.metadata`` (so the real composite foreign keys are in force), and drops it
afterwards.  It never touches ``public``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import make_url, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from prodavan.application.dynamic_app_plugin_gateway.contracts import (
    PluginHandle,
    PluginInvokeRequest,
    PluginOpenRequest,
)
from prodavan.domain.dynamic_apps import AppScope, AppScopeKind
from prodavan.infrastructure.persistence.dynamic_app_gateway import (
    SqlCapabilityScopePort,
    SqlDynamicPluginRepository,
)
from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)

from .seeds import NOW, grant, instance, manifest, version

ENV = "PRODAVAN_TEST_DATABASE_URL"
TENANT = "comp_aaaaaaaaaaaaaaaaaaaa"
EMPLOYEE = "emp_aaaaaaaaaaaaaaaaaaaa"
SUBJECT = "kc-subject-aaaa"
DEFINITION = "app_aaaaaaaaaaaaaaaaaaaa"
VERSION = "aver_aaaaaaaaaaaaaaaaaaaa"
INSTANCE = "ains_aaaaaaaaaaaaaaaaaaaa"
GRANT = "agr_aaaaaaaaaaaaaaaaaaaa"
SCOPE = AppScope(kind=AppScopeKind.TENANT, id=TENANT)


def _dsn() -> str:
    raw = (os.getenv(ENV) or "").strip()
    if not raw:
        pytest.skip(f"{ENV} is not set; this module only runs against PostgreSQL")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        pytest.skip(f"{ENV} is not a PostgreSQL DSN")
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def _reachable(dsn: str) -> bool:
    import socket

    url = make_url(dsn)
    try:
        with socket.create_connection(
            (url.host or "localhost", url.port or 5432), timeout=2.0
        ):
            return True
    except OSError:
        return False


class PgHarness:
    def __init__(self, engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]) -> None:
        self.engine = engine
        self.session_factory = factory
        self.repository = SqlDynamicPluginRepository(session_factory=factory)
        self.scope = SqlCapabilityScopePort(session_factory=factory, clock=lambda: NOW)


@pytest_asyncio.fixture
async def pg() -> AsyncIterator[PgHarness]:
    dsn = _dsn()
    if not _reachable(dsn):
        pytest.skip(f"nothing listens at {make_url(dsn).render_as_string(hide_password=True)}")

    schema = f"legacypg_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(dsn, poolclass=NullPool)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        dsn,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await _seed(factory)
        yield PgHarness(engine, factory)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    """Seed in the order the real composite foreign keys require."""

    body = manifest()
    async with factory() as session:
        session.add(CompanyRow(id=TENANT, name="Tenant A", login_slug="tenant-a"))
        session.add(
            EmployeeRow(
                id=EMPLOYEE,
                keycloak_sub=SUBJECT,
                login="member@tenant-a.local",
                email="member@tenant-a.local",
                status="active",
            )
        )
        await session.flush()
        session.add(MembershipRow(id="mem_1", employee_id=EMPLOYEE, company_id=TENANT))
        await session.commit()

    async with factory() as session:
        session.add(
            AppDefinitionRow(
                id=DEFINITION,
                tenant_id=TENANT,
                app_id="demo-plugin",
                display_name="Demo plugin",
                scope_kind="tenant",
                scope_id=TENANT,
                status="draft",
                current_version_id=None,
            )
        )
        await session.commit()
    async with factory() as session:
        session.add(
            version(
                row_id=VERSION,
                definition_id=DEFINITION,
                payload=body,
            )
        )
        await session.commit()
    async with factory() as session:
        await session.execute(
            update(AppDefinitionRow)
            .where(AppDefinitionRow.id == DEFINITION)
            .values(status="published", current_version_id=VERSION)
        )
        session.add(
            grant(
                row_id=GRANT,
                definition_id=DEFINITION,
                version_id=VERSION,
                scope_id=TENANT,
            )
        )
        session.add(
            instance(
                row_id=INSTANCE,
                definition_id=DEFINITION,
                version_id=VERSION,
                scope_id=TENANT,
            )
        )
        await session.commit()


def _open_request() -> PluginOpenRequest:
    return PluginOpenRequest(
        tenant_id=TENANT,
        app_id="demo-plugin",
        manifest_version="1.0.0",
        scope=SCOPE,
        subject_id=SUBJECT,
    )


def _invoke() -> PluginInvokeRequest:
    return PluginInvokeRequest(handle=PluginHandle(), method="kv.put")


async def test_jsonb_and_timestamptz_survive_the_port(pg: PgHarness) -> None:
    record = await pg.repository.resolve(_open_request())

    assert record is not None
    # JSONB -> mapping, and the capability payload -> typed grants.
    assert record.version.manifest.get("protocol") == "1.0"
    assert [entry.name.value for entry in record.grants[0].grants] == ["storage.kv"]
    assert [method.method for method in record.methods] == ["kv.put"]
    # timestamptz comes back aware; the domain would have raised otherwise.
    assert record.version.created_at.tzinfo is not None
    assert record.grants[0].issued_at.utcoffset() is not None


async def test_the_scope_port_allows_a_granted_member_on_postgres(pg: PgHarness) -> None:
    assert (
        await pg.scope.allows(
            tenant_id=TENANT, subject_id=SUBJECT, scope=SCOPE, request=_invoke()
        )
        is True
    )


async def test_the_scope_port_refuses_a_revoked_grant_on_postgres(pg: PgHarness) -> None:
    """Same row, same member, the capability denied: the answer must flip."""

    async with pg.session_factory() as session:
        await session.execute(
            update(AppGrantRow)
            .where(AppGrantRow.id == GRANT)
            .values(
                grants=[
                    {
                        "name": "storage.kv",
                        "decision": "denied",
                        "granted_by": "administrator",
                    }
                ]
            )
        )
        await session.commit()

    assert (
        await pg.scope.allows(
            tenant_id=TENANT, subject_id=SUBJECT, scope=SCOPE, request=_invoke()
        )
        is False
    )


async def test_an_archived_instance_is_refused_on_postgres(pg: PgHarness) -> None:
    async with pg.session_factory() as session:
        await session.execute(
            update(AppInstanceRow)
            .where(AppInstanceRow.id == INSTANCE)
            .values(status="disabled")
        )
        await session.commit()

    assert (
        await pg.scope.allows(
            tenant_id=TENANT, subject_id=SUBJECT, scope=SCOPE, request=_invoke()
        )
        is False
    )
    # …and the repository still *shows* it, so the facade can say "denied"
    # rather than "missing".
    record = await pg.repository.resolve(_open_request())
    assert record is not None and record.instance.status.value == "disabled"


async def test_a_foreign_tenant_reads_nothing_on_postgres(pg: PgHarness) -> None:
    request = PluginOpenRequest(
        tenant_id="comp_bbbbbbbbbbbbbbbbbbbb",
        app_id="demo-plugin",
        manifest_version="1.0.0",
        scope=SCOPE,
        subject_id=SUBJECT,
    )

    assert await pg.repository.resolve(request) is None


async def test_no_port_writes_on_postgres(pg: PgHarness) -> None:
    async with pg.session_factory() as session:
        before = (
            await session.execute(select(AppGrantRow.id).order_by(AppGrantRow.id))
        ).scalars().all()

    await pg.repository.resolve(_open_request())
    await pg.repository.get_grants(instance_id=INSTANCE, app_version_id=VERSION)
    await pg.scope.allows(
        tenant_id=TENANT, subject_id=SUBJECT, scope=SCOPE, request=_invoke()
    )

    async with pg.session_factory() as session:
        after = (
            await session.execute(select(AppGrantRow.id).order_by(AppGrantRow.id))
        ).scalars().all()
    assert before == after == [GRANT]


async def test_an_expired_grant_row_refuses_on_postgres(pg: PgHarness) -> None:
    """A ``timestamptz`` comparison across a real driver, not a SQLite round trip."""

    async with pg.session_factory() as session:
        await session.execute(
            update(AppGrantRow)
            .where(AppGrantRow.id == GRANT)
            .values(expires_at=NOW - timedelta(minutes=1))
        )
        await session.commit()

    assert (
        await pg.scope.allows(
            tenant_id=TENANT, subject_id=SUBJECT, scope=SCOPE, request=_invoke()
        )
        is False
    )
