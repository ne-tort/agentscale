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

Nothing here runs a migration or creates a table: the schema is *deliberately* absent, which
is what makes claim 3 testable.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prodavan.application.migration.composition import MigrationStage
from prodavan.application.migration.evidence_ports import (
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    TrustedEvidenceScope,
    bind_evidence_transaction,
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
    factory,
) -> None:
    """A missing relation is a redacted refusal, and it never reads as readiness.

    The database has no migration tables at all here, so the read must fail -- and the point
    is precisely *how* it fails: ``partition_evidence_blocked`` with an exception class, not
    an empty result (which would let an operator conclude "M04E has not started yet") and not
    an exception out of the runner.
    """

    source = SqlPartitionEvidenceSource(
        session_factory=factory,
        scope=TrustedEvidenceScope(tenant_id=key().tenant_id, writer_generation=GENERATION),
    )

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M05_INSTANCE_RUNTIME,
        session_factory=factory,
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
