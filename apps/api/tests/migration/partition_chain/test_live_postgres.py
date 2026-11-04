"""Live-PostgreSQL proofs for the per-partition entry point.

Everything else in this package is a pure unit test.  Three claims, however, are about the
*real* engine and cannot be established with doubles:

1. a partition's evidence stores construct over a real ``asyncpg`` session factory;
2. a read actually applies the trusted evidence scope first, so the transaction-local GUCs
   the write ports rely on really are set and read back on this server;
3. a relation that does not exist is reported as ``partition_evidence_blocked`` -- an
   unreadable store is never mistaken for "nothing recorded" and never for readiness.

The suite is skipped unless ``PRODAVAN_PARTITION_TEST_DATABASE_URL`` points at a disposable
database (the container in the task brief is one):

    docker run -d --name pg-test -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test \
        -e POSTGRES_DB=prodavan_test -p 55432:5432 postgres:16.15-alpine
    $env:PRODAVAN_PARTITION_TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/prodavan_test"

Nothing here runs a migration or creates a table.  Claim 3 needs a database where the
migration relations are *absent*, and the shared ``prodavan_test`` database is no longer such
a place: the chain is applied to it (``alembic_version`` and every migration table are
present), so "no relation exists" is not a property of the database any more.  A test may not
depend on the environment being empty -- that is an expectation about somebody else's run, not
about this code -- so the empty-database case builds its own throwaway schema and points
``search_path`` at it, exactly like ``tests/migration/auth_sessions/test_postgres_auth_sessions.py``.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from prodavan.application.migration.composition import MigrationStage
from prodavan.application.migration.evidence_ports import (
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    TrustedEvidenceScope,
    bind_evidence_transaction,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    M05HandoffMarker,  # noqa: F401 - imported so the marker decoder's module is loaded
)
from prodavan.application.migration.partition_chain import build_partition_chain
from prodavan.application.migration.partition_runner import (
    PARTITION_EVIDENCE_BLOCKED,
    SqlPartitionEvidenceSource,
    run_partition_stage,
)

from .partition_fixtures import GENERATION, complete_overrides, key

URL_ENV = "PRODAVAN_PARTITION_TEST_DATABASE_URL"

pytestmark = pytest.mark.skipif(
    not os.getenv(URL_ENV),
    reason=f"{URL_ENV} is not set; the per-partition live checks need a disposable database",
)


def _url() -> str:
    value = os.getenv(URL_ENV)
    assert value, URL_ENV
    return value


@pytest.fixture
async def factory():
    engine = create_async_engine(_url(), poolclass=None)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
async def unprovisioned_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A factory whose ``search_path`` holds an empty schema, created and dropped per test.

    Unqualified relation names in the adapters therefore resolve to nothing: this is a
    database where the migration chain was never applied, whatever the shared database
    happens to contain.
    """

    dsn = _url()
    url = make_url(dsn)
    try:
        with socket.create_connection(
            (url.host or "localhost", url.port or 5432), timeout=1.0
        ):
            pass
    except OSError:
        pytest.skip(f"nothing listens at {url.render_as_string(hide_password=True)}")

    schema = f"partition_empty_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            dsn,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()
    finally:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def test_the_trusted_scope_is_applied_and_read_back_on_a_real_server(factory) -> None:
    """The exact readback ``bind_evidence_transaction`` performs, against live PostgreSQL.

    If this fails, every write port of a partition is unsafe: the GUCs they read to prove
    their tenant would be absent or stale.
    """

    scope = TrustedEvidenceScope(tenant_id=key().tenant_id, writer_generation=GENERATION)
    async with factory() as session:
        async with session.begin():
            await bind_evidence_transaction(session, scope)
            row = (
                await session.execute(
                    text(
                        "SELECT current_setting('app.tenant_id', true) AS tenant_id, "
                        "current_setting('app.writer_generation', true) AS generation, "
                        "current_setting('app.migration_role', true) AS role"
                    )
                )
            ).mappings().one()

    assert row["tenant_id"] == key().tenant_id
    assert row["generation"] == str(GENERATION)
    assert row["role"] == "true"


async def test_a_partition_assembles_its_evidence_stores_over_a_real_factory(factory) -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=factory,
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert isinstance(plan.dependencies.m04e_m05_handoff, SqlM05HandoffEvidenceStore)
    assert isinstance(plan.dependencies.m05_promotion_records, SqlM05PromotionEvidenceStore)
    assert plan.complete is True


async def test_the_partition_evidence_source_refuses_its_own_tenant_without_a_schema(
    unprovisioned_factory,
) -> None:
    """A missing relation is a redacted refusal, and it never reads as readiness.

    ``unprovisioned_factory`` points ``search_path`` at an empty schema, so the migration
    relations genuinely do not exist for this test whatever the shared database contains.
    The read must therefore fail -- and the point is precisely *how* it fails:
    ``partition_evidence_blocked`` with an exception class, not an empty result (which would
    let an operator conclude "M04E has not started yet") and not an exception out of the
    runner.

    The earlier version of this case relied on the shared database having *no* migration
    tables at all.  That premise became false as soon as the chain was applied to it, and the
    runner then (correctly) reported ``partition_stage_precondition_unmet`` -- "no handoff
    marker yet" -- instead of ``partition_evidence_blocked``.  A test may not assert an
    unreadable store by depending on somebody else's migrations being absent; it has to
    create the unreadable state itself.
    """

    source = SqlPartitionEvidenceSource(
        session_factory=unprovisioned_factory,
        scope=TrustedEvidenceScope(tenant_id=key().tenant_id, writer_generation=GENERATION),
    )

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M05_INSTANCE_RUNTIME,
        session_factory=unprovisioned_factory,
        writer_generation=GENERATION,
        overrides=complete_overrides(),
        evidence_source=source,
    )

    assert outcome.executable is False
    assert outcome.blocked(PARTITION_EVIDENCE_BLOCKED)
    assert outcome.executed is False
    # The class name is reported; the message (which could carry the DSN) is not.
    assert "asyncpg" not in " ".join(outcome.details)


async def test_two_partitions_do_not_see_one_another_on_a_real_connection(factory) -> None:
    """Each partition's store opens its own transaction under its own GUCs."""

    alpha = SqlPartitionEvidenceSource(
        session_factory=factory,
        scope=TrustedEvidenceScope(tenant_id="tenant-alpha", writer_generation=GENERATION),
    )
    beta = SqlPartitionEvidenceSource(
        session_factory=factory,
        scope=TrustedEvidenceScope(tenant_id="tenant-beta", writer_generation=GENERATION),
    )

    async with alpha._read() as session:
        alpha_tenant = (
            await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
        ).scalar_one()
    async with beta._read() as session:
        beta_tenant = (
            await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
        ).scalar_one()

    assert alpha_tenant == "tenant-alpha"
    assert beta_tenant == "tenant-beta"
    assert alpha_tenant != beta_tenant
