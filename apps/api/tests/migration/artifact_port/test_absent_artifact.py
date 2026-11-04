"""An absent artifact is a *typed* refusal, never a silent ``None``.

The protocol says ``read_artifact`` returns ``None`` "when that object is absent" and that
"storage errors other than a confirmed missing object must raise".  Two obligations follow,
and both are tested here:

* ``None`` is reserved for **one** meaning -- a confirmed absence -- and the port's own
  ``read_artifact_verified`` turns even that into a reason code, so a caller that wants a
  proof never has to interpret ``None``;
* on a backend that cannot confirm a miss (the S3 store collapses every failure into
  ``FileNotFoundError``), a miss is *not* turned into ``None``: it is refused as
  ``artifact_store_missing_unconfirmed`` so the backfill keeps its checkpoint and retries,
  instead of quarantining a row on the strength of a network error.
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
    ARTIFACT_KEY,
    ARTIFACT_URI,
    CONTENT_URI,
    DEFINITION,
    TENANT,
    VERSION,
    CollapsingByteStore,
    FakeByteStore,
    RecordingSessionFactory,
    catalogue,
    content_asset,
    legacy_version,
)


def _port(
    objects: dict[str, bytes] | None = None,
    *,
    bytes_store: FakeByteStore | None = None,
    **catalogue_overrides: object,
) -> SqlArtifactContentStore:
    factory = RecordingSessionFactory(catalogue(**catalogue_overrides))
    store = bytes_store if bytes_store is not None else FakeByteStore(objects=dict(objects or {}))
    return SqlArtifactContentStore(session_factory=factory, file_store=store)


async def test_no_persisted_row_is_a_typed_refusal_not_an_empty_answer() -> None:
    """An empty answer would be indistinguishable from "this tenant has no such version"."""

    port = _port({ARTIFACT_KEY: ARTIFACT_BYTES}, legacy_versions=())

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_MISSING
    assert refusal.value.refusal.needed.strip()


async def test_resolve_returns_none_only_for_a_version_that_has_no_reference() -> None:
    """``None`` from ``resolve_artifact_uri`` means "the row carries no URI", nothing else."""

    port = _port(
        legacy_versions=(legacy_version(artifact_uri=None),),
    )

    resolved = await port.resolve_artifact_uri(
        tenant_id=TENANT, app_definition_id=DEFINITION, app_version_id=VERSION,
    )

    assert resolved is None


async def test_an_absent_version_is_reported_as_absent_by_resolve_not_as_a_refusal() -> None:
    """The resolver's contract is ``str | None``: a missing row is ``None``, ambiguity is not."""

    port = _port(legacy_versions=())

    resolved = await port.resolve_artifact_uri(
        tenant_id=TENANT, app_definition_id=DEFINITION, app_version_id=VERSION,
    )

    assert resolved is None


async def test_a_row_without_a_reference_cannot_be_read() -> None:
    port = _port(legacy_versions=(legacy_version(artifact_uri=None),))

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.URI_MISSING


async def test_a_confirmed_missing_object_is_the_one_none_read_artifact_returns() -> None:
    """``None`` is reserved for a confirmed miss; every other negative outcome raises."""

    port = _port({})  # the row exists, the object does not

    content = await port.read_artifact(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024 * 1024,
    )

    assert content is None


async def test_the_verified_read_names_the_confirmed_miss_instead_of_returning_none() -> None:
    port = _port({})

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_ABSENT


async def test_an_s3_backend_cannot_confirm_a_miss_so_it_is_not_turned_into_none() -> None:
    """A transient backend failure must not become a confirmed absence."""

    from .artifact_fixtures import CollapsingByteStore

    store = CollapsingByteStore(objects={})
    store.fail_with = RuntimeError("connection reset by peer")
    port = _port(bytes_store=store)

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.STORE_MISSING_UNCONFIRMED


async def test_a_local_backend_confirms_a_miss_because_it_cannot_collapse_errors() -> None:
    store = FakeByteStore(objects={})
    port = _port(bytes_store=store)

    assert port.missing_is_confirmed is True
    assert await port.read_artifact(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024,
    ) is None


async def test_the_host_may_override_the_missing_object_contract() -> None:
    """A store that fixes the ambiguity states it; the port honours the statement."""

    from .artifact_fixtures import CollapsingByteStore

    store = CollapsingByteStore(objects={})
    store.fail_with = RuntimeError("outage")
    factory = RecordingSessionFactory(catalogue())
    port = SqlArtifactContentStore(
        session_factory=factory, file_store=store, missing_is_confirmed=True,
    )

    assert port.missing_is_confirmed is True
    assert await port.read_artifact(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024,
    ) is None


async def test_a_lazily_resolved_store_decides_the_absence_contract_not_the_field() -> None:
    """With no store bound the read resolves one lazily -- and *that* store's backend counts.

    This is the failure mode a naive implementation has: the port would consult its own
    (``None``) ``file_store`` field, conclude "no collapsing backend", and turn a transient
    S3 outage into a confirmed absence -- i.e. quarantine a healthy row.
    """

    from prodavan.infrastructure.files import manager as file_manager

    collapsing = CollapsingByteStore(objects={})
    collapsing.fail_with = RuntimeError("connection reset by peer")
    # ``ensure_file_store`` returns a manager it considers *started*, i.e. one whose
    # ``_primary`` is set; the double must look like that or the lazy path would create a
    # second, real manager instead of using the store this test is about.
    collapsing._primary = collapsing  # type: ignore[attr-defined]
    previous = file_manager.get_file_store_optional()
    file_manager.set_file_store(collapsing)  # type: ignore[arg-type]
    try:
        port = SqlArtifactContentStore(
            session_factory=RecordingSessionFactory(catalogue()),
            file_store=None,
        )
        # The property itself must not resolve (and therefore must not start) the store.
        assert port.missing_is_confirmed is True

        with pytest.raises(ArtifactContentRefused) as refusal:
            await port.read_artifact(
                tenant_id=TENANT,
                app_definition_id=DEFINITION,
                app_version_id=VERSION,
                artifact_uri=ARTIFACT_URI,
                max_bytes=1024,
            )
    finally:
        file_manager.set_file_store(previous)

    assert refusal.value.code is ArtifactRefusalCode.STORE_MISSING_UNCONFIRMED


async def test_a_backend_error_that_is_not_a_miss_is_never_reported_as_absence() -> None:
    store = FakeByteStore(objects={ARTIFACT_KEY: ARTIFACT_BYTES})
    store.fail_with = OSError("disk on fire")
    port = _port(bytes_store=store)

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.STORE_READ_FAILED


async def test_a_store_that_returns_a_non_byte_payload_is_refused() -> None:
    class StringStore(FakeByteStore):
        def get_bytes(self, key: str) -> bytes:  # type: ignore[override]
            self.reads.append(key)
            return "not bytes"  # type: ignore[return-value]

    port = _port(bytes_store=StringStore(objects={ARTIFACT_KEY: ARTIFACT_BYTES}))

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.STORE_READ_FAILED


async def test_a_content_asset_that_does_not_exist_is_named_as_such() -> None:
    port = _port(
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.ASSET_ABSENT


async def test_a_content_asset_without_a_blob_version_is_named_as_such() -> None:
    port = _port(
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(content_asset(),),
        content_blobs=(),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.ASSET_BLOB_ABSENT


@pytest.mark.parametrize(
    "uri",
    (
        "s3://bucket/key",
        "https://example.invalid/bundle.zip",
        "content://",  # a content reference with no asset id
        "/just/a/path",
    ),
)
async def test_a_reference_scheme_without_a_reviewed_mapping_is_refused(uri: str) -> None:
    """No storage key is guessed from a URI shape; an unknown scheme names what is needed."""

    port = _port(
        {ARTIFACT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(artifact_uri=uri),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=uri,
            max_bytes=1024,
        )

    assert refusal.value.code in {
        ArtifactRefusalCode.SCHEME_UNSUPPORTED,
        ArtifactRefusalCode.URI_UNRESOLVED,
    }
    assert "mapping" in refusal.value.refusal.needed.lower()


@pytest.mark.parametrize("max_bytes", (0, -1, 1.5, True, "1024", None))
async def test_an_invalid_read_bound_is_refused_before_any_read(max_bytes: object) -> None:
    port = _port({ARTIFACT_KEY: ARTIFACT_BYTES})

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=max_bytes,  # type: ignore[arg-type]
        )

    assert refusal.value.code is ArtifactRefusalCode.MAX_BYTES_INVALID
