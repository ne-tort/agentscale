"""A foreign artifact is refused, not answered with an empty result.

Fail-closed here means four independent layers, and each one is asserted separately so a
regression in one cannot hide behind another:

1. **identity** -- the tuple must name exactly one persisted row of *this* tenant;
2. **tenant context** -- the transaction-local GUC must be bound and read back before any
   table read, because ``FORCE ROW LEVEL SECURITY`` would otherwise return zero rows and
   layer 1 would misread that as "absent";
3. **SQL predicate** -- every statement carries an explicit ``tenant_id``;
4. **content ownership** -- a ``content://`` asset must be owned by the partition tenant.

The last layer has a subtlety worth its own test: an asset with ``owner_company_id IS NULL``
is *platform* content.  It is readable in principle, but it is not tenant-immutable evidence
for this partition, so it is refused as unproven rather than treated as owned.
"""

from __future__ import annotations

import pytest

from prodavan.infrastructure.persistence.migration_artifact_store import (
    ArtifactContentRefused,
    ArtifactRefusalCode,
    SqlArtifactContentStore,
)
from .artifact_fixtures import (
    ARTIFACT_BYTES,
    ARTIFACT_DIGEST,
    ARTIFACT_KEY,
    ARTIFACT_URI,
    CONTENT_KEY,
    CONTENT_URI,
    DEFINITION,
    FakeByteStore,
    OTHER_DIGEST,
    OTHER_TENANT,
    RecordingSessionFactory,
    TENANT,
    VERSION,
    catalogue,
    content_asset,
    content_blob,
    legacy_version,
    target_version,
)


def _port(
    objects: dict[str, bytes] | None = None,
    *,
    bytes_store: FakeByteStore | None = None,
    **catalogue_overrides: object,
) -> tuple[SqlArtifactContentStore, FakeByteStore, RecordingSessionFactory]:
    factory = RecordingSessionFactory(catalogue(**catalogue_overrides))
    store = bytes_store if bytes_store is not None else FakeByteStore(
        objects=dict(objects) if objects is not None else {ARTIFACT_KEY: ARTIFACT_BYTES}
    )
    return SqlArtifactContentStore(session_factory=factory, file_store=store), store, factory


@pytest.mark.parametrize(
    ("tenant_id", "definition_id", "version_id"),
    (
        (OTHER_TENANT, DEFINITION, VERSION),
        (TENANT, "appdef_other", VERSION),
        (TENANT, DEFINITION, "aver_other"),
    ),
)
async def test_an_identity_outside_the_tenant_is_a_refusal_not_an_empty_answer(
    tenant_id: str, definition_id: str, version_id: str,
) -> None:
    """A foreign identity must not look like "nothing to read" -- that leaks nothing but
    hides everything: an operator would conclude the row does not exist."""

    port, store, _factory = _port()

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=tenant_id,
            app_definition_id=definition_id,
            app_version_id=version_id,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_MISSING
    assert store.reads == []


async def test_a_row_that_reports_another_tenant_is_refused_even_if_sql_returned_it() -> None:
    """Defence in depth: a widened read (policy regression) must not yield bytes."""

    port, store, _factory = _port(
        legacy_versions=(legacy_version(tenant_id=OTHER_TENANT),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_MISSING
    assert store.reads == []


async def test_a_row_that_reports_another_definition_is_refused() -> None:
    port, store, _factory = _port(
        legacy_versions=(legacy_version(definition_id="appdef_other"),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_MISSING
    assert store.reads == []


async def test_an_unbindable_tenant_context_is_refused_before_the_read() -> None:
    """Without a proven GUC the version tables return zero rows under ``FORCE RLS``.

    Zero rows would be reported as ``IDENTITY_MISSING`` -- i.e. "this tenant has no such
    version" -- which is a false statement about a row that exists.  The port refuses
    instead, so the caller keeps its checkpoint.
    """

    port, store, factory = _port()
    factory.catalogue.tenant_readback = "tenant-somebody-else"

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.TENANT_CONTEXT_UNBOUND
    assert store.reads == []
    # No table was read at all: the context check comes first.
    assert not [sql for sql in factory.statements if "FROM dynamic_app_versions" in sql]


async def test_a_context_whose_role_marker_did_not_apply_is_refused() -> None:
    port, store, factory = _port()
    factory.catalogue.role_readback = "false"

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.TENANT_CONTEXT_UNBOUND
    assert store.reads == []


async def test_every_version_statement_carries_an_explicit_tenant_predicate() -> None:
    """RLS is the guard, not the only guard: the SQL itself is tenant-bounded."""

    from prodavan.infrastructure.persistence.migration_artifact_store import (
        SELECT_LEGACY_VERSION_SQL,
        SELECT_TARGET_VERSION_SQL,
    )

    assert "d.tenant_id = :tenant_id" in str(SELECT_LEGACY_VERSION_SQL)
    assert "tenant_id = :tenant_id" in str(SELECT_TARGET_VERSION_SQL)
    # The legacy version table has no tenant column of its own: it is bounded through its
    # owning definition, so the statement must join that relation.
    assert "dynamic_app_definitions" in str(SELECT_LEGACY_VERSION_SQL)


async def test_a_content_asset_owned_by_another_tenant_is_refused() -> None:
    port, store, _factory = _port(
        {CONTENT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(content_asset(owner_company_id=OTHER_TENANT),),
        content_blobs=(content_blob(),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.ASSET_TENANT_MISMATCH
    assert store.reads == []


async def test_a_platform_owned_content_asset_is_not_tenant_immutable_evidence() -> None:
    """``owner_company_id IS NULL`` is readable in principle -- but not proof for *this* tenant."""

    port, store, _factory = _port(
        {CONTENT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(content_asset(owner_company_id=None, owner_scope="platform"),),
        content_blobs=(content_blob(),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.ASSET_TENANT_UNPROVEN
    assert store.reads == []
    assert "platform" in refusal.value.refusal.needed.lower()


async def test_a_foreign_asset_cannot_be_reached_through_a_tenant_owned_version_row() -> None:
    """The version row is the tenant's; the object it points at is not. Both must be owned."""

    port, store, _factory = _port(
        {CONTENT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(content_asset(owner_company_id=OTHER_TENANT),),
        content_blobs=(content_blob(),),
    )

    # ``read_artifact`` is the protocol method the backfill actually calls: it must not
    # degrade a foreign asset into ``None`` ("absent") either.
    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.ASSET_TENANT_MISMATCH
    assert store.reads == []


@pytest.mark.parametrize(
    ("tenant_id", "definition_id", "version_id"),
    (
        ("", DEFINITION, VERSION),
        ("  ", DEFINITION, VERSION),
        (" tenant-padded", DEFINITION, VERSION),
        (TENANT, "", VERSION),
        (TENANT, DEFINITION, " "),
    ),
)
async def test_an_empty_or_padded_identity_is_refused(
    tenant_id: str, definition_id: str, version_id: str,
) -> None:
    port, store, factory = _port()

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=tenant_id,
            app_definition_id=definition_id,
            app_version_id=version_id,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_INVALID
    assert store.reads == []
    assert factory.calls == 0


async def test_two_canonical_rows_for_one_identity_are_ambiguous_not_merged() -> None:
    port, store, _factory = _port(
        target_versions=(target_version(), target_version()),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_AMBIGUOUS
    assert store.reads == []


async def test_a_refusal_carries_the_partition_it_refused_and_nothing_else() -> None:
    """A refusal is a redacted record: a reason code, a partition, a digest -- no payload."""

    port, _store, _factory = _port(
        legacy_versions=(),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri="object://blobs/secret-key-do-not-log",
            max_bytes=1024,
        )

    text = refusal.value.refusal.evidence_text()
    assert refusal.value.refusal.tenant_id == TENANT
    assert refusal.value.refusal.app_definition_id == DEFINITION
    # The storage key is a capability: it must not appear in a refusal meant for audit.
    assert "secret-key-do-not-log" not in text
    assert refusal.value.code.value in text
