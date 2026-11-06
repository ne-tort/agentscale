"""Live-PostgreSQL proof: the composed port really reads a version's artifact.

Every other test in this package answers from a double, which is right for the decision
table but says nothing about the statements themselves.  Two claims can only be made against
a real engine:

1. the identity tuple resolves through a real ``JOIN`` of ``dynamic_app_versions`` on its
   owning definition, and through the canonical ``m04e_dynamic_app_versions`` row;
2. the transaction-local GUC really is applied and read back on this server, so the port's
   tenant binding is not a Python-only convention.

The suite creates its own two-table fixture (plus the two content tables for the
``content://`` path) with ``CREATE TABLE IF NOT EXISTS``/``DROP TABLE`` inside the disposable
database, and it never runs Alembic: this is a scratch schema in a scratch database, and the
task forbids touching models or revisions.

Skipped unless ``PRODAVAN_PARTITION_TEST_DATABASE_URL`` is set::

    docker run -d --name pg-test -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test \\
        -e POSTGRES_DB=prodavan_test -p 55432:5432 postgres:16.15-alpine
    $env:PRODAVAN_PARTITION_TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/prodavan_test"
"""

from __future__ import annotations

import hashlib
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prodavan.application.migration.partition_chain import (
    PARTITION_M04E_ARTIFACTS_UNAVAILABLE,
    PartitionChainKey,
    build_partition_chain,
)
from prodavan.infrastructure.persistence.migration_artifact_store import (
    ArtifactContentRefused,
    ArtifactRefusalCode,
    SqlArtifactContentStore,
)

from ..partition_chain.partition_fixtures import GENERATION, complete_overrides
from .artifact_fixtures import (
    ARTIFACT_BYTES,
    ARTIFACT_DIGEST,
    ARTIFACT_KEY,
    ARTIFACT_URI,
    CONTENT_KEY,
    CONTENT_URI,
    DEFINITION,
    OTHER_TENANT,
    TENANT,
    VERSION,
    AsyncByteStore,
    digest_of,
)

URL_ENV = "PRODAVAN_PARTITION_TEST_DATABASE_URL"

pytestmark = pytest.mark.skipif(
    not os.getenv(URL_ENV),
    reason=f"{URL_ENV} is not set; the live artifact checks need a disposable database",
)

#: The fixture lives in its **own PostgreSQL schema**, not in ``public``.
#:
#: The disposable database is shared: other migration suites apply the real migration chain
#: to ``public``, where ``content_alias_bindings`` and friends already reference
#: ``content_blob_versions``.  Truncating our tables there fails on those foreign keys, and
#: dropping them would destroy another suite's schema.  ``search_path`` is therefore pinned
#: to a private schema, so the port's unqualified statements resolve against *these* tables
#: and nothing else is touched.  This is a scratch schema in a scratch database: no model,
#: no revision and no Alembic state is involved.
SCRATCH_SCHEMA = "artifact_port_scratch"

#: Column names and types mirror the registered models closely enough for the port's
#: statements; nothing here is a migration and nothing is committed to it.
_DDL = (
    """
    CREATE TABLE IF NOT EXISTS dynamic_app_definitions (
        id varchar(80) PRIMARY KEY,
        tenant_id varchar(80) NOT NULL,
        app_id varchar(160) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dynamic_app_versions (
        id varchar(80) PRIMARY KEY,
        app_definition_id varchar(80) NOT NULL REFERENCES dynamic_app_definitions(id),
        manifest_version varchar(128) NOT NULL,
        artifact_digest varchar(71) NOT NULL,
        artifact_uri varchar(2048)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS m04e_dynamic_app_versions (
        id varchar(80) NOT NULL,
        definition_id varchar(80) NOT NULL,
        tenant_id varchar(40) NOT NULL,
        manifest_version varchar(128) NOT NULL,
        artifact_digest varchar(71) NOT NULL,
        artifact_uri varchar(2048),
        PRIMARY KEY (id, definition_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content_assets (
        id varchar(40) PRIMARY KEY,
        owner_company_id varchar(40),
        owner_scope varchar(32) NOT NULL DEFAULT 'company',
        visibility varchar(32) NOT NULL DEFAULT 'company'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS content_blob_versions (
        id varchar(40) PRIMARY KEY,
        asset_id varchar(40) NOT NULL REFERENCES content_assets(id),
        version integer NOT NULL,
        storage_key varchar(512) NOT NULL,
        size integer NOT NULL DEFAULT 0,
        sha256 varchar(64)
    )
    """,
)

_TRUNCATE = (
    "TRUNCATE content_blob_versions, content_assets, m04e_dynamic_app_versions, "
    "dynamic_app_versions, dynamic_app_definitions"
)


def _url() -> str:
    value = os.getenv(URL_ENV)
    assert value, URL_ENV
    return value


def _engine():
    """An engine pinned to the scratch schema, so unqualified SQL resolves there first."""

    return create_async_engine(
        _url(),
        poolclass=None,
        connect_args={"server_settings": {"search_path": f"{SCRATCH_SCHEMA},public"}},
    )


@pytest.fixture
async def engine():
    scratch = _engine()
    try:
        async with scratch.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCRATCH_SCHEMA}"))
        async with scratch.begin() as connection:
            for statement in _DDL:
                await connection.execute(text(statement))
            await connection.execute(text(_TRUNCATE))
        yield scratch
    finally:
        await scratch.dispose()


@pytest.fixture
async def factory(engine):
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text(_TRUNCATE))


async def _seed_legacy(engine, *, tenant: str = TENANT, uri: str = ARTIFACT_URI) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO dynamic_app_definitions (id, tenant_id, app_id) "
                "VALUES (:id, :tenant_id, 'demo-app')"
            ),
            {"id": DEFINITION, "tenant_id": tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO dynamic_app_versions "
                "(id, app_definition_id, manifest_version, artifact_digest, artifact_uri) "
                "VALUES (:id, :definition, '1.0.0', :digest, :uri)"
            ),
            {"id": VERSION, "definition": DEFINITION, "digest": ARTIFACT_DIGEST, "uri": uri},
        )


def _port(factory, objects: dict[str, bytes] | None = None) -> SqlArtifactContentStore:
    """A port over an in-memory byte store.

    ``objects`` is compared with ``None`` rather than tested for truth: an **empty** dict is
    a meaningful argument here ("the store holds nothing"), and ``objects or default`` would
    silently turn the confirmed-absence test green-for-the-wrong-reason by supplying the
    artifact it was supposed to be missing.
    """

    store = AsyncByteStore(objects={} if objects is None else dict(objects))
    if objects is None:
        store.objects = {ARTIFACT_KEY: ARTIFACT_BYTES}
    return SqlArtifactContentStore(session_factory=factory, file_store=store)


async def test_a_matching_artifact_is_read_from_a_live_database(engine, factory) -> None:
    await _seed_legacy(engine)

    read = await _port(factory).read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.content == ARTIFACT_BYTES
    assert read.digest == digest_of(ARTIFACT_BYTES)


async def test_the_uri_is_resolved_by_a_real_join_on_the_owning_definition(
    engine, factory,
) -> None:
    await _seed_legacy(engine)

    resolved = await _port(factory).resolve_artifact_uri(
        tenant_id=TENANT, app_definition_id=DEFINITION, app_version_id=VERSION,
    )

    assert resolved == ARTIFACT_URI


async def test_a_foreign_tenant_reads_nothing_and_is_told_so(engine, factory) -> None:
    """The other tenant's row exists in the same table; it must be unreachable."""

    await _seed_legacy(engine)

    with pytest.raises(ArtifactContentRefused) as refusal:
        await _port(factory).read_artifact_verified(
            tenant_id=OTHER_TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_MISSING


async def test_mutated_bytes_in_the_store_are_refused_on_a_real_read(engine, factory) -> None:
    await _seed_legacy(engine)

    with pytest.raises(ArtifactContentRefused) as refusal:
        await _port(factory, {ARTIFACT_KEY: b"tampered at rest"}).read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_DIGEST_MISMATCH


async def test_a_missing_object_is_a_confirmed_absence_on_the_local_backend(
    engine, factory,
) -> None:
    await _seed_legacy(engine)

    content = await _port(factory, {}).read_artifact(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert content is None


async def test_the_canonical_row_is_read_through_its_own_tenant_predicate(
    engine, factory,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO m04e_dynamic_app_versions "
                "(id, definition_id, tenant_id, manifest_version, artifact_digest, artifact_uri) "
                "VALUES (:id, :definition, :tenant, '1.0.0', :digest, :uri)"
            ),
            {
                "id": VERSION, "definition": DEFINITION, "tenant": TENANT,
                "digest": ARTIFACT_DIGEST, "uri": ARTIFACT_URI,
            },
        )

    read = await _port(factory).read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.digest == ARTIFACT_DIGEST


async def test_a_conflicting_canonical_row_is_refused_against_the_legacy_one(
    engine, factory,
) -> None:
    await _seed_legacy(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO m04e_dynamic_app_versions "
                "(id, definition_id, tenant_id, manifest_version, artifact_digest, artifact_uri) "
                "VALUES (:id, :definition, :tenant, '1.0.0', :digest, :uri)"
            ),
            {
                "id": VERSION, "definition": DEFINITION, "tenant": TENANT,
                "digest": "sha256:" + "0" * 64, "uri": ARTIFACT_URI,
            },
        )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await _port(factory).read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_CONFLICT


async def test_a_content_reference_resolves_through_the_content_tables(
    engine, factory,
) -> None:
    await _seed_legacy(engine, uri=CONTENT_URI)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO content_assets (id, owner_company_id) "
                "VALUES ('cast_0001', :tenant)"
            ),
            {"tenant": TENANT},
        )
        await connection.execute(
            text(
                "INSERT INTO content_blob_versions "
                "(id, asset_id, version, storage_key, size, sha256) "
                "VALUES ('cbv_0001', 'cast_0001', 1, :key, :size, :sha)"
            ),
            {
                "key": CONTENT_KEY,
                "size": len(ARTIFACT_BYTES),
                "sha": ARTIFACT_DIGEST.removeprefix("sha256:"),
            },
        )

    read = await _port(factory, {CONTENT_KEY: ARTIFACT_BYTES}).read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=CONTENT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.content == ARTIFACT_BYTES
    assert read.byte_source == "content_asset"


async def test_a_foreign_tenant_owned_asset_is_refused_on_a_real_read(engine, factory) -> None:
    await _seed_legacy(engine, uri=CONTENT_URI)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO content_assets (id, owner_company_id) "
                "VALUES ('cast_0001', :tenant)"
            ),
            {"tenant": OTHER_TENANT},
        )
        await connection.execute(
            text(
                "INSERT INTO content_blob_versions "
                "(id, asset_id, version, storage_key, size, sha256) "
                "VALUES ('cbv_0001', 'cast_0001', 1, :key, :size, :sha)"
            ),
            {
                "key": CONTENT_KEY,
                "size": len(ARTIFACT_BYTES),
                "sha": ARTIFACT_DIGEST.removeprefix("sha256:"),
            },
        )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await _port(factory, {CONTENT_KEY: ARTIFACT_BYTES}).read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.ASSET_TENANT_MISMATCH


async def test_the_tenant_guc_is_really_applied_and_read_back_on_this_server(
    engine, factory,
) -> None:
    """The binding is a database fact, not a Python convention."""

    async with factory() as session:
        async with session.begin():
            from prodavan.infrastructure.persistence.migration_artifact_store import (
                APPLY_CONTEXT_SQL,
                READBACK_CONTEXT_SQL,
            )

            await session.execute(APPLY_CONTEXT_SQL, {
                "tenant_id": TENANT, "user_id": "operator", "role": "m04e_artifact_read",
            })
            row = (await session.execute(READBACK_CONTEXT_SQL)).mappings().one()

    assert row["tenant_id"] == TENANT
    assert row["migration_role"] == "true"


async def test_the_partition_planner_composes_a_port_that_reads_this_database(
    engine, factory,
) -> None:
    """End to end: ``build_partition_chain`` -> the composed port -> real bytes.

    The partition key and the seeded rows must name the same identity, so the planner is
    given the *fixture's* partition rather than the suite-wide placeholder one.
    """

    await _seed_legacy(engine)
    partition = PartitionChainKey(tenant_id=TENANT, app_definition_id=DEFINITION)
    plan = build_partition_chain(
        key=partition,
        session_factory=factory,
        writer_generation=GENERATION,
        **complete_overrides(m04e_artifacts=None).chain_kwargs(),
    )

    assert PARTITION_M04E_ARTIFACTS_UNAVAILABLE not in plan.blocker_codes
    port = plan.dependencies.m04e_artifacts
    assert isinstance(port, SqlArtifactContentStore)

    # The composed port has no object store bound: it resolves one lazily on the first byte
    # read, and a test must not trigger that against the real storage root.  Resolution and
    # identity proof need no store, so the database half is proven here...
    resolved = await port.resolve_artifact_uri(
        tenant_id=partition.tenant_id,
        app_definition_id=partition.app_definition_id,
        app_version_id=VERSION,
    )
    assert resolved == ARTIFACT_URI

    # ...and the byte half is proven through the same class with a store the test owns.
    read = await _port(factory).read_artifact_verified(
        tenant_id=partition.tenant_id,
        app_definition_id=partition.app_definition_id,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.content == ARTIFACT_BYTES
    assert hashlib.sha256(read.content).hexdigest() == ARTIFACT_DIGEST.removeprefix("sha256:")
