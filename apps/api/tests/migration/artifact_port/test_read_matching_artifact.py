"""A matching artifact is read, and its immutability is *proven* rather than assumed.

The positive path has three obligations, and each is asserted separately:

* the bytes handed back are exactly the stored ones;
* the digest is recomputed from those bytes and returned, so the caller never has to trust
  a value the store reported;
* the URI used is the one **persisted on the version row**, not the caller's argument -- and
  a caller that supplies a different but equally well-formed URI is refused instead of read.
"""

from __future__ import annotations

import pytest

from prodavan.infrastructure.persistence.migration_artifact_store import (
    ArtifactContentRefused,
    ArtifactRefusalCode,
    SqlArtifactContentStore,
    build_m04e_artifact_content_store,
)

from .artifact_fixtures import (
    ARTIFACT_BYTES,
    ARTIFACT_DIGEST,
    ARTIFACT_KEY,
    ARTIFACT_URI,
    CONTENT_KEY,
    CONTENT_URI,
    DEFINITION,
    TENANT,
    VERSION,
    AsyncByteStore,
    FakeByteStore,
    RecordingSessionFactory,
    catalogue,
    content_asset,
    content_blob,
    digest_of,
    legacy_version,
    target_version,
)


def _store(objects: dict[str, bytes] | None = None, **catalogue_overrides):
    factory = RecordingSessionFactory(catalogue(**catalogue_overrides))
    bytes_store = FakeByteStore(objects={ARTIFACT_KEY: ARTIFACT_BYTES})
    if objects is not None:
        bytes_store.objects = dict(objects)
    return (
        SqlArtifactContentStore(session_factory=factory, file_store=bytes_store),
        factory,
        bytes_store,
    )


async def test_the_exact_artifact_bytes_are_returned_with_their_digest() -> None:
    port, _factory, bytes_store = _store()

    read = await port.read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.content == ARTIFACT_BYTES
    # The digest is the one computed from the bytes that came back, not a copy of the row.
    assert read.digest == digest_of(ARTIFACT_BYTES) == ARTIFACT_DIGEST
    assert read.tenant_id == TENANT
    assert read.app_definition_id == DEFINITION
    assert read.app_version_id == VERSION
    assert bytes_store.reads == [ARTIFACT_KEY]


async def test_read_artifact_returns_the_same_bytes_the_verified_call_proves() -> None:
    port, _factory, _bytes_store = _store()

    content = await port.read_artifact(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert content == ARTIFACT_BYTES


async def test_the_uri_is_resolved_from_the_persisted_row_only() -> None:
    port, _factory, _bytes_store = _store()

    resolved = await port.resolve_artifact_uri(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
    )

    assert resolved == ARTIFACT_URI


async def test_a_caller_supplied_uri_that_is_not_the_persisted_one_is_refused() -> None:
    """A caller-selected reference is not an artifact identity, however plausible it looks."""

    port, _factory, bytes_store = _store(objects={
        ARTIFACT_KEY: ARTIFACT_BYTES,
        "blobs/somebody-elses": b"not this artifact",
    })

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri="object://blobs/somebody-elses",
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.URI_MISMATCH
    # Nothing was read: the identity check happened before the transfer.
    assert bytes_store.reads == []


async def test_the_content_scheme_reads_the_tenant_owned_asset_blob() -> None:
    """``content://<asset>`` resolves through the content tables, not by parsing a key."""

    port, _factory, bytes_store = _store(
        objects={CONTENT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(content_asset(),),
        content_blobs=(content_blob(),),
    )

    read = await port.read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=CONTENT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.content == ARTIFACT_BYTES
    assert read.byte_source == "content_asset"
    assert bytes_store.reads == [CONTENT_KEY]


async def test_the_object_scheme_records_the_object_store_as_the_byte_source() -> None:
    port, _factory, _bytes_store = _store()

    read = await port.read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.byte_source == "object_store"


async def test_an_async_file_store_is_supported_like_a_sync_one() -> None:
    """``FileStoreManager`` is async and the raw backend stores are sync; both are accepted."""

    factory = RecordingSessionFactory(catalogue())
    bytes_store = AsyncByteStore(objects={ARTIFACT_KEY: ARTIFACT_BYTES})
    port = SqlArtifactContentStore(session_factory=factory, file_store=bytes_store)

    read = await port.read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.digest == ARTIFACT_DIGEST


async def test_the_canonical_row_alone_is_enough_when_the_legacy_one_is_gone() -> None:
    """The backfill binds a legacy version, but the adapter is not legacy-only."""

    port, _factory, _bytes_store = _store(
        legacy_versions=(),
        target_versions=(target_version(),),
    )

    read = await port.read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.content == ARTIFACT_BYTES


async def test_both_rows_are_accepted_when_they_agree() -> None:
    port, _factory, _bytes_store = _store(
        legacy_versions=(legacy_version(),),
        target_versions=(target_version(),),
    )

    read = await port.read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert read.digest == ARTIFACT_DIGEST


async def test_the_tenant_context_is_bound_before_any_table_is_read() -> None:
    """The GUC is set first, so ``FORCE ROW LEVEL SECURITY`` cannot answer with zero rows.

    An unbound tenant would make the version tables return nothing, and the port would then
    report "absent" for a row that exists -- a false finding.  The assertion is on ordering.
    """

    port, factory, _bytes_store = _store()

    await port.resolve_artifact_uri(
        tenant_id=TENANT, app_definition_id=DEFINITION, app_version_id=VERSION,
    )

    statements = factory.statements
    set_index = next(i for i, sql in enumerate(statements) if "set_config('app.tenant_id'" in sql)
    readback_index = next(
        i for i, sql in enumerate(statements) if "current_setting('app.tenant_id'" in sql
    )
    table_index = next(
        i for i, sql in enumerate(statements) if "FROM dynamic_app_versions" in sql
    )
    assert set_index < readback_index < table_index
    assert factory.sessions[0].context is not None
    assert factory.sessions[0].context["tenant_id"] == TENANT


async def test_assembly_opens_no_session() -> None:
    """Constructing the port is not reading it -- the planner relies on that."""

    factory = RecordingSessionFactory()

    port = build_m04e_artifact_content_store(session_factory=factory)

    assert isinstance(port, SqlArtifactContentStore)
    assert factory.calls == 0


def test_a_port_cannot_be_built_without_a_session_factory() -> None:
    assert build_m04e_artifact_content_store(session_factory=None) is None
