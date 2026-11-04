"""A digest mismatch is refused: the store is never believed.

This is the immutability proof of the whole task.  The M04E backfill persists
``artifact_digest`` on an immutable version row; the artifact port's job is to make sure the
bytes that come *back* hash to exactly that value, so a mutated object, a re-pointed
``storage_key`` or a substituted asset cannot become a different artifact identity.

Three mismatch shapes are covered, because each one is a different failure mode:

* the object changed but the row did not (bytes are wrong);
* the row and the object agree with each other but not with what was sealed for the asset
  (the content row's own ``sha256`` catches it *before* the transfer);
* the legacy and canonical rows disagree on the identity (the port refuses rather than
  picking one of two conflicting truths).
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
    RecordingSessionFactory,
    TENANT,
    VERSION,
    catalogue,
    content_asset,
    content_blob,
    digest_of,
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
    if bytes_store is None:
        bytes_store = FakeByteStore(
            objects=dict(objects) if objects is not None else {ARTIFACT_KEY: ARTIFACT_BYTES}
        )
    return SqlArtifactContentStore(session_factory=factory, file_store=bytes_store), bytes_store, factory


async def test_bytes_that_do_not_hash_to_the_persisted_digest_are_refused() -> None:
    """The central claim: a returned digest is computed, and it is checked."""

    mutated = b'{"protocol":"1.0","appId":"demo-app","extra":"tampered"}'
    port, _store, _factory = _port({ARTIFACT_KEY: mutated})

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_DIGEST_MISMATCH
    # The refusal names why the bytes are unusable without echoing them.
    assert digest_of(mutated) in refusal.value.refusal.detail
    assert mutated.decode() not in refusal.value.refusal.detail


async def test_a_mismatch_is_never_returned_as_bytes() -> None:
    """``read_artifact`` must not degrade a mismatch into "some bytes" or ``None``."""

    port, _store, _factory = _port({ARTIFACT_KEY: b"different bytes entirely"})

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_DIGEST_MISMATCH


async def test_a_content_asset_sealed_to_another_digest_is_refused_before_the_read() -> None:
    """The asset's own sealed ``sha256`` is a cheaper, earlier proof than the transfer."""

    port, bytes_store, _factory = _port(
        {CONTENT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(content_asset(),),
        content_blobs=(content_blob(sha256=OTHER_DIGEST.removeprefix("sha256:")),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.ASSET_DIGEST_MISMATCH
    # No transfer: the wrong asset was rejected on metadata alone.
    assert bytes_store.reads == []


async def test_a_content_asset_without_a_sealed_digest_still_gets_the_byte_proof() -> None:
    """An unsealed asset is not trusted *more* -- the bytes are hashed as usual."""

    port, bytes_store, _factory = _port(
        {CONTENT_KEY: b"unsealed and wrong"},
        legacy_versions=(legacy_version(artifact_uri=CONTENT_URI),),
        content_assets=(content_asset(),),
        content_blobs=(content_blob(sha256=None),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=CONTENT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_DIGEST_MISMATCH
    assert bytes_store.reads == [CONTENT_KEY]


async def test_the_legacy_and_canonical_rows_must_agree_on_the_artifact_identity() -> None:
    """Two persisted truths that disagree are a refusal, not a coin flip."""

    port, bytes_store, _factory = _port(
        {ARTIFACT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(),),
        target_versions=(target_version(artifact_digest=OTHER_DIGEST),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_CONFLICT
    assert bytes_store.reads == []


async def test_a_canonical_row_with_another_uri_is_an_identity_conflict() -> None:
    port, bytes_store, _factory = _port(
        {ARTIFACT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(),),
        target_versions=(target_version(artifact_uri="object://blobs/other"),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.IDENTITY_CONFLICT
    assert bytes_store.reads == []


@pytest.mark.parametrize(
    "digest",
    (
        "",
        "sha256:",
        "sha256:short",
        "md5:" + "a" * 32,
        "sha256:" + "A" * 64,  # uppercase hex is not the agreed form
        None,
    ),
)
async def test_a_persisted_digest_that_is_not_sha256_is_refused(digest: object) -> None:
    port, bytes_store, _factory = _port(
        {ARTIFACT_KEY: ARTIFACT_BYTES},
        legacy_versions=(legacy_version(artifact_digest=digest),),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.DIGEST_MISSING
    assert bytes_store.reads == []


async def test_an_object_larger_than_the_declared_bound_is_refused_before_transfer() -> None:
    """A head-based size check keeps an oversized object from being pulled at all."""

    bytes_store = FakeByteStore(objects={ARTIFACT_KEY: ARTIFACT_BYTES})
    bytes_store.sizes[ARTIFACT_KEY] = 10 * 1024 * 1024
    port, store, _factory = _port(bytes_store=bytes_store)

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_EXCEEDS_LIMIT
    assert store.reads == []
    assert store.heads == [ARTIFACT_KEY]


async def test_an_object_larger_than_the_limit_is_refused_after_transfer_too() -> None:
    """A store without a head still cannot smuggle an oversized artifact past the bound."""

    bytes_store = FakeByteStore(objects={ARTIFACT_KEY: b"x" * 4096})

    class NoHead(FakeByteStore):
        head = None  # type: ignore[assignment]

    port = SqlArtifactContentStore(
        session_factory=RecordingSessionFactory(catalogue()),
        file_store=NoHead(objects=dict(bytes_store.objects)),
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_EXCEEDS_LIMIT


async def test_the_callers_limit_is_capped_by_the_port_bound() -> None:
    """A caller cannot widen the read past the bound the port was constructed with."""

    port = SqlArtifactContentStore(
        session_factory=RecordingSessionFactory(catalogue()),
        file_store=FakeByteStore(objects={ARTIFACT_KEY: ARTIFACT_BYTES}),
        max_object_bytes=8,
    )

    with pytest.raises(ArtifactContentRefused) as refusal:
        await port.read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=ARTIFACT_URI,
            max_bytes=64 * 1024 * 1024,
        )

    assert refusal.value.code is ArtifactRefusalCode.CONTENT_EXCEEDS_LIMIT
