"""A refusal is a redacted record: no payload, no URI body, no secret reaches a log.

Two rules are checked, and both are about what a *support engineer* would see:

* an ``ArtifactRefusal`` carries a reason code, the partition tuple and (where relevant) a
  digest -- never an object key, never a byte of the artifact, never a token;
* the adapter's own ``warning`` on a digest mismatch names the partition and the version,
  because that is the actionable part, and nothing else.

The second rule matters more than it looks: the object key *is* the capability on the local
backend (``blobs/<uuid>`` is the whole address), so a log line that echoes a URI would leak
read access to every artifact the log is shipped to.
"""

from __future__ import annotations

import logging

import pytest

from prodavan.infrastructure.persistence.migration_artifact_store import (
    ArtifactContentRefused,
    SqlArtifactContentStore,
    _uri_scheme,
)

from .artifact_fixtures import (
    ARTIFACT_BYTES,
    ARTIFACT_KEY,
    ARTIFACT_URI,
    DEFINITION,
    TENANT,
    VERSION,
    FakeByteStore,
    RecordingSessionFactory,
    catalogue,
    legacy_version,
)

SECRET_KEY = "blobs/capability-that-must-not-be-logged"

#: Substrings that must never appear in a refusal or a log line.
FORBIDDEN = (
    SECRET_KEY,
    ARTIFACT_KEY,
    "capability-that-must-not-be-logged",
    ARTIFACT_BYTES.decode(),
    "protocol",
)


def _port(**catalogue_overrides: object) -> SqlArtifactContentStore:
    return SqlArtifactContentStore(
        session_factory=RecordingSessionFactory(catalogue(**catalogue_overrides)),
        file_store=FakeByteStore(objects={SECRET_KEY: b"tampered"}),
    )


async def test_a_digest_mismatch_logs_the_partition_and_never_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ArtifactContentRefused):
        await _port(
            legacy_versions=(legacy_version(artifact_uri=f"object://{SECRET_KEY}"),),
        ).read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=f"object://{SECRET_KEY}",
            max_bytes=1024,
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for needle in FORBIDDEN:
        assert needle not in logged, f"{needle!r} leaked into the log"
    # The actionable identity *is* logged: which partition failed.
    assert TENANT in logged
    assert DEFINITION in logged


async def test_a_refusal_message_carries_no_key_and_no_payload() -> None:
    with pytest.raises(ArtifactContentRefused) as refusal:
        await _port(
            legacy_versions=(legacy_version(artifact_uri=f"object://{SECRET_KEY}"),),
        ).read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=f"object://{SECRET_KEY}",
            max_bytes=1024,
        )

    rendered = str(refusal.value) + refusal.value.refusal.evidence_text()
    for needle in FORBIDDEN:
        assert needle not in rendered, f"{needle!r} leaked into a refusal"


@pytest.mark.parametrize(
    ("uri", "expected"),
    (
        ("object://blobs/x", "object://"),
        ("file:///tmp/x", "file://"),
        ("content://cast_1", "content://"),
        ("s3://bucket/key", "s3://"),
        ("not-a-uri", "<opaque>"),
        ("", "<opaque>"),
    ),
)
def test_a_scheme_is_the_only_part_of_a_uri_that_may_be_named(
    uri: str, expected: str,
) -> None:
    assert _uri_scheme(uri) == expected


async def test_an_unsupported_scheme_refusal_names_the_scheme_not_the_reference() -> None:
    uri = f"weird://{SECRET_KEY}"
    with pytest.raises(ArtifactContentRefused) as refusal:
        await _port(
            legacy_versions=(legacy_version(artifact_uri=uri),),
        ).read_artifact_verified(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            artifact_uri=uri,
            max_bytes=1024,
        )

    rendered = str(refusal.value)
    assert "weird://" in rendered
    assert SECRET_KEY not in rendered


async def test_the_successful_read_does_not_log_the_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    port = SqlArtifactContentStore(
        session_factory=RecordingSessionFactory(catalogue()),
        file_store=FakeByteStore(objects={ARTIFACT_KEY: ARTIFACT_BYTES}),
    )

    await port.read_artifact_verified(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        artifact_uri=ARTIFACT_URI,
        max_bytes=1024,
    )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert ARTIFACT_BYTES.decode() not in logged
