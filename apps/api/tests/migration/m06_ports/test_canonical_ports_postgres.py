"""Real-PostgreSQL acceptance for the canonical grant/company ports (opt-in).

Task ``MIG-M06-PORTS-01``.

The SQLite-free unit suite proves the *decision*: which rows are returned, which
company resolves, what a malformed row does.  It cannot prove the **types**, and
those are exactly what a canonical grant read depends on:

* ``operations``/``resources``/``domains`` are ``JSONB``; a decode that produced
  the wrong Python shape would pass on a mirror and fail here;
* every timestamp is ``timestamptz``, and ``CanonicalGrant`` refuses a naive
  value — so a regression surfaces as ``CanonicalGrantReadRefused``, not as a
  silently weaker grant.

The suite is skipped unless ``PRODAVAN_TEST_DATABASE_URL`` names a reachable
database.  It builds a throwaway ``canonicalpg_<hex>`` schema from the production
``Base.metadata``, drops the five exact-identity foreign keys of the grant table
**inside that schema only** (see :func:`_drop_exact_identity_foreign_keys`), and
never touches ``public``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import make_url, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from prodavan.infrastructure.persistence.canonical_grants_read import (
    CanonicalGrantReadRefused,
    SqlAuthenticatedCompanyPort,
    SqlCanonicalGrantReadPort,
)
from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.canonical_grants import CanonicalGrantRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow

ENV = "PRODAVAN_TEST_DATABASE_URL"

TENANT = "comp_aaaaaaaaaaaaaaaaaaaa"
OTHER_TENANT = "comp_bbbbbbbbbbbbbbbbbbbb"
DEFINITION = "app_aaaaaaaaaaaaaaaaaaaa"
VERSION = "aver_aaaaaaaaaaaaaaaaaaaa"
INSTANCE = "ains_aaaaaaaaaaaaaaaaaaaa"
GRANT_ID = "cgrant_aaaaaaaaaaaaaaaaaaaa"
APP_ID = "demo-plugin"
ARTIFACT = "sha256:" + "a" * 64
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

#: The five exact-identity constraints of ``dynamic_app_capability_grants``.
#: They are what makes the real schema refuse an orphan grant; this module is about
#: JSONB/timestamptz round-trip through the port, and the constraints themselves are
#: proven elsewhere (``tests/migration/canonical_grants``), so the schema of this
#: throwaway database relaxes them rather than seeding four unrelated chains.
EXACT_IDENTITY_CONSTRAINTS = (
    "fk_cap_grant_version_definition_artifact",
    "fk_cap_grant_scope_registry",
    "fk_cap_grant_definition",
    "fk_cap_grant_definition_tenant",
    "fk_cap_grant_instance_exact",
)


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
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=2.0):
            return True
    except OSError:
        return False


class PgHarness:
    def __init__(
        self, engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.engine = engine
        self.session_factory = factory
        self.company = SqlAuthenticatedCompanyPort(session_factory=factory)
        self.grants = SqlCanonicalGrantReadPort(session_factory=factory)


@pytest_asyncio.fixture
async def pg() -> AsyncIterator[PgHarness]:
    dsn = _dsn()
    if not _reachable(dsn):
        pytest.skip(f"nothing listens at {make_url(dsn).render_as_string(hide_password=True)}")

    schema = f"canonicalpg_{uuid.uuid4().hex[:10]}"
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
            await _drop_exact_identity_foreign_keys(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await _seed(factory)
        yield PgHarness(engine, factory)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def _drop_exact_identity_foreign_keys(connection) -> None:
    """Relax only the exact-identity FKs, inside this throwaway schema."""

    for constraint in EXACT_IDENTITY_CONSTRAINTS:
        await connection.execute(
            text(
                "ALTER TABLE dynamic_app_capability_grants "
                f"DROP CONSTRAINT IF EXISTS {constraint}"
            )
        )


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        session.add(CompanyRow(id=TENANT, name="Tenant A", login_slug="canonical-a"))
        session.add(
            CompanyRow(
                id=OTHER_TENANT,
                name="Tenant B",
                login_slug="canonical-b",
                status="suspended",
            )
        )
        await session.commit()

    async with factory() as session:
        session.add(_grant())
        await session.commit()


def _grant(**overrides) -> CanonicalGrantRow:
    values = {
        "id": GRANT_ID,
        "app_definition_id": DEFINITION,
        "app_id": APP_ID,
        "app_version_id": VERSION,
        "app_artifact_digest": ARTIFACT,
        "tenant_id": TENANT,
        "scope_registry_id": "sr_aaaaaaaaaaaaaaaaaaaa",
        "scope_kind": "plugin_instance",
        "scope_id": INSTANCE,
        "capability_key": "storage.kv",
        # ``plugin_instance`` scope shape requires ``scope_id = plugin_instance_id``.
        "subject_kind": "user",
        "subject_id": "kc-subject-aaaa",
        "plugin_instance_id": INSTANCE,
        "session_id": "sess-1",
        "operations": ["write"],
        "resources": ["drafts"],
        "domains": ["storage"],
        "constraints": {},
        "operations_digest": ARTIFACT,
        "resources_digest": ARTIFACT,
        "domains_digest": ARTIFACT,
        "constraints_digest": ARTIFACT,
        "issued_at": NOW,
        "issued_by": "administrator",
        "grant_generation": 1,
        "writer_generation": 1,
        "idempotency_key": "idem-1",
        "schema_version": "m06.v1",
        "payload_digest": ARTIFACT,
    }
    values.update(overrides)
    return CanonicalGrantRow(**values)


# ---------------------------------------------------------------------------
# AuthenticatedCompanyPort
# ---------------------------------------------------------------------------


async def test_a_live_company_resolves_to_its_tenant_id(pg: PgHarness) -> None:
    assert await pg.company.resolve_tenant_id(company_id=TENANT) == TENANT


async def test_a_non_active_company_is_not_a_tenant_on_postgres(pg: PgHarness) -> None:
    """``companies.status`` is the pause state; a paused company is not a tenant."""

    assert await pg.company.resolve_tenant_id(company_id=OTHER_TENANT) is None


async def test_an_unknown_or_blank_company_id_resolves_to_nothing(pg: PgHarness) -> None:
    assert await pg.company.resolve_tenant_id(company_id="comp_ffffffffffffffffffff") is None
    assert await pg.company.resolve_tenant_id(company_id="   ") is None


async def test_a_soft_deleted_company_is_not_a_tenant_on_postgres(pg: PgHarness) -> None:
    async with pg.session_factory() as session:
        company = await session.get(CompanyRow, TENANT)
        assert company is not None
        company.deleted_at = NOW
        await session.commit()

    assert await pg.company.resolve_tenant_id(company_id=TENANT) is None


# ---------------------------------------------------------------------------
# CanonicalGrantReadPort
# ---------------------------------------------------------------------------


async def test_jsonb_and_timestamptz_survive_the_canonical_read(pg: PgHarness) -> None:
    grants = await pg.grants.list_grants(
        tenant_id=TENANT, app_id=APP_ID, app_version_id=VERSION, plugin_instance_id=INSTANCE
    )

    assert len(grants) == 1
    grant = grants[0]
    # JSONB arrays -> frozen, hashable sets of the exact declared values.
    assert grant.operations == frozenset({"write"})
    assert grant.resources == frozenset({"drafts"})
    assert grant.domains == frozenset({"storage"})
    assert grant.constraints == {}
    # timestamptz comes back aware; ``CanonicalGrant`` would have raised otherwise.
    assert grant.issued_at.utcoffset() is not None
    assert grant.issued_at == NOW
    assert grant.expires_at is None and grant.revoked_at is None


async def test_the_read_is_scoped_by_all_four_keys(pg: PgHarness) -> None:
    """A row must not be reachable through a neighbouring key of the same tenant."""

    assert (
        await pg.grants.list_grants(
            tenant_id=TENANT,
            app_id="another-plugin",
            app_version_id=VERSION,
            plugin_instance_id=INSTANCE,
        )
        == ()
    )
    assert (
        await pg.grants.list_grants(
            tenant_id=TENANT,
            app_id=APP_ID,
            app_version_id="aver_bbbbbbbbbbbbbbbbbbbb",
            plugin_instance_id=INSTANCE,
        )
        == ()
    )
    assert (
        await pg.grants.list_grants(
            tenant_id=TENANT,
            app_id=APP_ID,
            app_version_id=VERSION,
            plugin_instance_id="ains_bbbbbbbbbbbbbbbbbbbb",
        )
        == ()
    )
    assert (
        await pg.grants.list_grants(
            tenant_id=OTHER_TENANT,
            app_id=APP_ID,
            app_version_id=VERSION,
            plugin_instance_id=INSTANCE,
        )
        == ()
    )


async def test_a_revoked_row_is_still_returned_so_the_decision_can_say_revoked(
    pg: PgHarness,
) -> None:
    """Filtering a revocation out would rewrite policy as "nothing there"."""

    async with pg.session_factory() as session:
        row = await session.get(CanonicalGrantRow, GRANT_ID)
        assert row is not None
        row.revoked_at = NOW + timedelta(minutes=1)
        row.revoked_by = "administrator"
        row.revoke_reason = "incident"
        await session.commit()

    grants = await pg.grants.list_grants(
        tenant_id=TENANT, app_id=APP_ID, app_version_id=VERSION, plugin_instance_id=INSTANCE
    )

    assert len(grants) == 1
    assert grants[0].revoked_at is not None
    from prodavan.application.canonical_grants.contracts import (
        CanonicalGrantReason,
    )

    assert grants[0].check(
        operation="write",
        resource="drafts",
        domain="storage",
        constraints={},
        subject_id="kc-subject-aaaa",
        session_id="sess-1",
        now=NOW + timedelta(minutes=2),
    ) is CanonicalGrantReason.REVOKED


async def test_an_undecodable_row_is_a_refusal_not_a_skipped_row(pg: PgHarness) -> None:
    """A dropped row would read as "grant missing", which is a false denial.

    ``CanonicalGrant`` refuses an artifact digest that is not sha256; the port must
    therefore refuse the *read*, not quietly return the surviving rows.
    """

    async with pg.session_factory() as session:
        row = await session.get(CanonicalGrantRow, GRANT_ID)
        assert row is not None
        # Bypass the CHECK by rewriting through raw SQL: the point is a row that a
        # different writer could have produced, not one this schema permits.
        await session.execute(
            text(
                "ALTER TABLE dynamic_app_capability_grants "
                "DROP CONSTRAINT IF EXISTS ck_cap_grant_artifact_sha256"
            )
        )
        await session.execute(
            text(
                "UPDATE dynamic_app_capability_grants SET app_artifact_digest = :digest "
                "WHERE id = :id"
            ),
            {"digest": "not-a-digest", "id": GRANT_ID},
        )
        await session.commit()

    with pytest.raises(CanonicalGrantReadRefused):
        await pg.grants.list_grants(
            tenant_id=TENANT, app_id=APP_ID, app_version_id=VERSION, plugin_instance_id=INSTANCE
        )


async def test_no_port_writes_on_postgres(pg: PgHarness) -> None:
    async with pg.session_factory() as session:
        before = (await session.execute(select(CanonicalGrantRow.id))).scalars().all()

    await pg.grants.list_grants(
        tenant_id=TENANT, app_id=APP_ID, app_version_id=VERSION, plugin_instance_id=INSTANCE
    )
    await pg.company.resolve_tenant_id(company_id=TENANT)

    async with pg.session_factory() as session:
        after = (await session.execute(select(CanonicalGrantRow.id))).scalars().all()
    assert before == after == [GRANT_ID]
